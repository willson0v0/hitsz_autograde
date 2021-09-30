from json.decoder import JSONDecodeError
import os
from os import path, replace
import json
import logging
import argparse
from posixpath import isabs
import subprocess
import shutil
from threading import Thread, Semaphore, Lock
import re
import glob
import zipfile
import csv
import mosspy

LESSDEBUG_LOG_LEVEL = 15

class CustomFormatter(logging.Formatter):
    grey        = "\x1b[90m"
    white       = "\x1b[38m"
    green       = "\x1b[92m"
    yellow      = "\x1b[93m"
    red         = "\x1b[91;1m"
    bold_red    = "\x1b[97;41m"
    reset       = "\x1b[0m"
    format      = "[ %(asctime)s ] [ %(levelname)s ]:\t%(message)s"

    FORMATS = {
        logging.DEBUG: grey + format + reset,
        LESSDEBUG_LOG_LEVEL: white + format + reset,
        logging.INFO: green + format + reset,
        logging.WARNING: yellow + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


def less_debug(self, message, *args, **kws):
    if self.isEnabledFor(LESSDEBUG_LOG_LEVEL):
        self._log(LESSDEBUG_LOG_LEVEL, message, args, **kws) 


ch = logging.StreamHandler()
logger = logging.getLogger()

class DotDict(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

class Grader:
    # args.parallel: Parallel grading job count.
    # args.config: DotDict file path. Normally under config/ folder.
    def __init__(self, args):
        logger.info("正在初始化批量评测脚本……")
        self.parallel_count = args.parallel
        self.real_path = path.dirname(os.path.realpath(__file__))
        self.grading_env_path = path.join(self.real_path, "grading_envs")
        self.moss_path = path.join(self.real_path, "moss_path")
        self.clean_xv6_path = path.join(self.grading_env_path, "clean_xv6")
        self.config_file = args.config if path.isabs(args.config) else path.join(self.real_path, args.config)
        self.config_file_base = path.dirname(path.realpath(self.config_file))
        self.stu_files_folder = args.student_files if path.isabs(args.student_files) else path.join(self.real_path, args.student_files)
        self.output_dir = args.output_dir if path.isabs(args.output_dir) else path.join(self.real_path, args.output_dir)
        self.codex = args.codex
        self.plagiarism_threshold = args.plagiarism_threshold
        self.anonymous = args.anonymous
        try:
            with open(self.config_file, "r") as cf:
                self.config = DotDict(json.loads(cf.read()))
                for file, f_conf in self.config.plagiarism_test.items():
                    if f_conf["template"] and not path.isabs(f_conf["template"]):
                        self.config.plagiarism_test[file]["template"] = path.join(self.config_file_base, f_conf["template"])
                    self.config.plagiarism_test[file]["known_solutions"] = [
                        (f if path.isabs(f) else path.join(self.config_file_base, f)) 
                        for f in self.config.plagiarism_test[file]["known_solutions"]
                    ]
                    self.config.plagiarism_test[file] = DotDict(self.config.plagiarism_test[file])
                self.config.overrides = [DotDict(f) for f in self.config.overrides]
                for override in self.config.overrides:
                    override.operation = DotDict(override.operation)
                if not self.config.moss_report_path:
                    self.config.moss_report_path = "moss_report"
                if not path.isabs(self.config.moss_report_path):
                    self.config.moss_report_path = path.join(self.real_path, self.config.moss_report_path)
        except FileNotFoundError:
            logger.fatal("未找到对应配置文件。")
            exit(0)
        except JSONDecodeError:
            logger.fatal("配置文件不是合法的JSON文件。")
            exit(0)
        logger.verbose("已加载配置文件。")
        self.explain_config()
        self.semaphore = Semaphore(self.parallel_count)
        self.env_available = []
        for i in range(0, self.parallel_count):
            self.env_available.append(Lock())
        self.result_mutex = Lock()
        self.output_mutex = Lock()
        self.results = {}
        self.bad_files = []
        self.report_url = {}
    

    def setup_env(self):
        logger.info("正在构造评测环境……")

        if os.path.exists(self.grading_env_path):
            shutil.rmtree(self.grading_env_path)
        
        os.mkdir(self.grading_env_path)
        os.mkdir(self.clean_xv6_path)

        logger.debug("正在下载实验测试环境……")
        try:
            subprocess.check_output(["git", "clone", "-b", self.config.branch, self.config.repo, self.clean_xv6_path])
        except subprocess.CalledProcessError:
            logger.fatal("实验测试环境配置失败。")
            exit(0)
        
        logger.debug("正在构造查重检查文件夹……")
        
        if os.path.exists(self.moss_path):
            shutil.rmtree(self.moss_path)
        os.mkdir(self.moss_path)

        for file, conf in self.config.plagiarism_test.items():
            os.mkdir(path.join(self.moss_path, file))
            for sol in conf.known_solutions:
                shutil.copy(sol, path.join(self.moss_path, file))
        
        if os.path.exists(self.config.script_output):
            shutil.rmtree(self.config.script_output)
        os.mkdir(self.config.script_output)

    def batch_grade(self):
        logger.info("开始准备批量评测……")
        logger.debug("正在读取提交文件……")
        student_filenames = [f for f in os.listdir(self.stu_files_folder)]
        threads = []
        for f in student_filenames:
            self.semaphore.acquire()
            env = self.alloc_env()
            logger.verbose(f"检测到提交文件{f}，使用{env}号评测环境。")
            t = Thread(target=self.single_grade, args=(env, f))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        logger.info("评测已全部完成。开始导出成绩与执行失败列表。")
        try:
            if not path.exists(self.output_dir):
                os.mkdir(self.output_dir)
            score_path = path.join(self.output_dir, "score.csv")
            with open(score_path, "w", encoding=self.codex) as score_file:
                writer = csv.writer(score_file)
                writer.writerow(["学号", "姓名", "得分", "注释"])
                for file_name, (score, err_msg) in self.results.items():
                    parse_regex = r"^([a-zA-Z0-9]{4,12})_([\w\u4e00-\u9fa5\u2000-\u206F]{2,30})_file\.zip$"
                    match_res = re.match(parse_regex, file_name)
                    writer.writerow([match_res.groups()[0], match_res.groups()[1], score, err_msg])
            logger.info(f"成绩已保存至{score_path}。")
            bad_path = path.join(self.output_dir, "bad_files.csv")
            with open(bad_path, "w", encoding=self.codex) as bad_list:
                writer = csv.writer(bad_list)
                for bad in self.bad_files:
                    writer.writerow([bad,])
            logger.info(f"异常列表已保存至{bad_path}。")
        except Exception as e:
            logger.fatal(f"未能成功保存评测结果。错误信息如下：{e}")
    

    def plagiarism_test(self):
        for to_check, conf in self.config.plagiarism_test.items():
            logger.debug(f"开始对{to_check}执行代码查重。")
            moss_client = mosspy.Moss(self.config.moss_userid, "c")
            if conf.template:
                moss_client.addBaseFile(conf.template)
            for sol in conf.known_solutions:
                moss_client.addFile(sol)
            moss_client.addFilesByWildcard(f"{path.join(self.moss_path, to_check)}/*{to_check}")
            logger.info(f"正在发送{to_check}的代码，以进行代码查重。")
            report_url = moss_client.send()
            logger.info(f"{to_check}的代码查重报告已生成，报告URL为{report_url}。")
            self.report_url[to_check] = report_url
    

    def visualize_plagiarism(self):
        logger.info(f"正在生成可视化查重结果...")
        leading_cmd = ["mossum", "-f", "svg", "-t", ".*/(.*)_(.*)_.*", "-m", "-p", "90", "-o", f"{self.config.moss_report_path}"]
        if self.anonymous:
            leading_cmd.append("-a")
        mossum_proc = subprocess.Popen(leading_cmd + [url for _, url in self.report_url.items()], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with mossum_proc.stdout:
            for line in iter(mossum_proc.stdout.readline, b''):
                logging.debug(f"mossum output: {line}")
        exitcode = mossum_proc.wait() # 0 means success
        if exitcode == 0:
            logger.info(f"可视化查重结果已生成于{self.config.moss_report_path}")
        else:
            logger.info(f"可视化查重结果生成失败。")

    
    def alloc_env(self):
        for i, lock in enumerate(self.env_available):
            if lock.acquire(blocking=False):
                return i
        
        logger.fatal("找不到空闲的评测环境。")
        exit(0)

    def free_env(self, env_id):
        self.env_available[env_id].release()

        
    def single_grade(self, env_id, student_file):
        env_path = path.join(self.grading_env_path, f"env{env_id}")
        env_judge_path = path.join(env_path, f"clean_xv6")
        env_stu_path = path.join(env_path, f"stu")
        orig_stu_path = path.join(self.stu_files_folder, student_file)
        score = 0
        
        parse_regex = r"^([a-zA-Z0-9]{5,12})_([\w\u4e00-\u9fa5]{2,20})_file\.zip$"
        match_res = re.match(parse_regex, student_file)

        name = None
        stu_id = None
        err_msg = []

        if not match_res:
            logger.warning(f"检测到不符合命名规则的文件{student_file}。")
            self.result_mutex.acquire()
            self.bad_files.append(student_file)
            self.result_mutex.release()
            self.free_env(env_id)
            self.semaphore.release()
            return
        else:
            name = match_res.groups()[1]
            stu_id = match_res.groups()[0]
            logger.debug(f"评测环境{env_id}开始对{name}（{stu_id}）的提交文件执行测试。")

        logger.verbose(f"正在初始化并行评测环境{env_id}……")
        if path.exists(env_path):
            shutil.rmtree(env_path)
        os.mkdir(env_path)
        shutil.copytree(self.clean_xv6_path, env_judge_path)
        
        logger.verbose(f"正在将{orig_stu_path}解压至{env_stu_path}")
        try:
            with zipfile.ZipFile(orig_stu_path, 'r') as zip_ref:
                zip_ref.extractall(env_stu_path)
            
            compressed_files = glob.glob(env_stu_path + "/**/*.zip", recursive=True) + glob.glob(env_stu_path + "/**/*.rar", recursive=True)
            while compressed_files:
                logger.debug(f"在{name}（{stu_id}）的提交文件中发现嵌套压缩包{[path.basename(f) for f in compressed_files]}。正在重新解压。")
                for cf in compressed_files:
                    if cf.endswith(".zip"):
                        with zipfile.ZipFile(cf, 'r') as zip_ref:
                            zip_ref.extractall(env_stu_path)
                    elif cf.endswith(".rar"):
                        subprocess.check_call(["unrar", "x", cf, env_stu_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    os.remove(cf)
                compressed_files = glob.glob(env_stu_path + "/**/*.zip", recursive=True) + glob.glob(env_stu_path + "/**/*.rar", recursive=True)
        except Exception as e:
            logger.error(f"无法完成对{name}（{stu_id}）的提交文件的解压。失败原因：{e}")
            err_msg.append(f"无法解压，因为'{e}'")
        
        logger.verbose(f"正在将需要查重的学生文件重命名并复制至查重文件夹……")

        for file in self.config.plagiarism_test:
            self.find_copy(file, env_stu_path, path.join(self.moss_path, file, f"{stu_id}_{name}_{file}"))
        
        logger.verbose(f"正在构造评测环境……")

        logger.verbose(f"正在复制需要学生新建的文件……")
        missing_files = []
        for file_name, dst in self.config.new_file.items():
            try:
                os.remove(path.join(env_judge_path, dst))
            except OSError:
                pass
            
            find_count = self.find_copy(file_name, env_stu_path, path.join(env_judge_path, dst))
            if find_count == 0:
                logger.debug(f"未在{name}（{stu_id}）的提交中找到需要新建的源文件{file_name}。")
                missing_files.append(file_name)
            elif find_count != 1:
                logger.debug(f"在{name}（{stu_id}）的提交中发现多个{file_name}，任选其一。")
                err_msg.append(f"发现多个{file_name}")
        
        logger.verbose(f"正在替换需要学生更改的文件……")
        for file_name, dst in self.config.alter_file.items():
            find_count = self.find_copy(file_name, env_stu_path, path.join(env_judge_path, dst))
            if find_count == 0:
                logger.debug(f"未在{name}（{stu_id}）的提交中找到需要替换的源文件{file_name}。将使用评测环境中的源文件代替。")
                missing_files.append(file_name)
            elif find_count != 1:
                logger.debug(f"在{name}（{stu_id}）的提交中发现多个{file_name}，任选其一。")
                err_msg.append(f"发现多个{file_name}")
        
        if missing_files:
            logger.warning(f"{name}（{stu_id}）的提交中缺少下列文件：{missing_files}")
            err_msg.append(f"缺少文件：{missing_files}")
        
        logger.verbose(f"正在根据配置最终覆写评测环境……")
        for override_item in self.config.overrides:
            to_override = path.join(env_judge_path, override_item.file_path)
            if override_item.operation.type == "alteration":
                try:
                    replaced_txt = None
                    original_expanded = override_item.operation.original.format(env_id=env_id, stu_id=stu_id, name=name)
                    altered_expanded = override_item.operation.altered.format(env_id=env_id, stu_id=stu_id, name=name)
                    with open(to_override, "r") as rf:
                        replaced_txt = rf.read()
                    if not replaced_txt.find(original_expanded):
                        logger.error(f"在替换{override_item.file_path}时，未能找到应替换的字串{original_expanded}")
                        raise
                    replaced_txt = replaced_txt.replace(original_expanded, altered_expanded)
                    with open(to_override, "w") as wf:
                        wf.write(replaced_txt)
                except Exception as e:
                    logger.error(f"替换{to_override}的内容时出现错误。")
                    err_msg.append(f"替换{to_override}的内容时出现错误")
                logger.verbose(f"完成对{to_override}内容的替换。")
            elif override_item.operation.type == "creation":
                to_create = path.join(env_judge_path, override_item.file_path)
                try:
                    content_expanded = override_item.operation.content.format(env_id=env_id, stu_id=stu_id, name=name)
                    with open(to_create, "w") as wf:
                        wf.write(content_expanded)
                except Exception as e:
                    logger.error(f"生成{to_create}时出现错误：{e}。")
                    err_msg.append(f"生成{to_create}时出现错误")
                logger.verbose(f"完成对{to_create}的生成。")
            
        logger.debug(f"评测环境{env_id}构造完成，开始评测。")
        
        p = subprocess.run([path.join(env_judge_path, self.config.test_script)], cwd=env_judge_path, capture_output=True)

        score_out = [line.decode('utf-8') for line in p.stdout.split(b'\n')]
        found = False
        for line in score_out:
            score_match_res = re.match(self.config.result_regex, line)
            if score_match_res:
                score = score_match_res.groups()[0]
                found = True
                break

        self.output_mutex.acquire()

        if not found:
            logger.error(f"在运行{name}（{stu_id}）的提交时，评测脚本执行失败，0分。")
            err_msg.append(f"评测脚本执行失败")
        else:
            logger.info(f"{name}（{stu_id}）的提交评测完成，{score}分。")
            err_msg.append(f"评测脚本执行成功")
        
        logger.verbose(f"评测脚本输出（stdout）：")
        for line in p.stdout.split(b'\n'):
            logger.verbose(f"\t{line}")
        logger.verbose(f"评测脚本输出（stderr）：")
        for line in p.stderr.split(b'\n'):
            logger.verbose(f"\t{line}")
        
        with open(path.join(self.config.script_output, f"{student_file}_outputlog.txt"), "w") as log_file:
            log_file.write("\n===== stdout =====\n")
            log_file.write(p.stdout.decode(self.codex))
            log_file.write("\n===== stderr =====\n")
            log_file.write(p.stderr.decode(self.codex))

        self.output_mutex.release()

        self.result_mutex.acquire()
        self.results[student_file] = (score, ";".join(err_msg))
        if score == 0:
            self.bad_files.append(student_file)
        self.result_mutex.release()
        self.free_env(env_id)
        self.semaphore.release()
        return
    

    def find_copy(self, file_name, src_dir, dst_path):
        matches = glob.glob(src_dir+"/**/"+file_name, recursive=True)
        if not matches:
            return 0
        
        if len(matches) != 1:
            logger.debug(f"发现多个{file_name}文件，选择{matches[0]}。")

        shutil.copy(matches[0], dst_path)
        logger.verbose(f"将文件{matches[0]}拷贝至{dst_path}")
        return len(matches)

    
    def explain_config(self):
        logger.debug("==================== 批量评测配置 ====================")
        logger.debug(f"评测脚本位置: {self.config_file}")
        logger.debug(f"输出文件编码: {self.codex}")

        logger.debug("")
        logger.debug("==================== 提交文件配置 ====================")
        logger.debug("")

        if not self.config.new_file:
            logger.debug("不允许学生创建新文件。")
        else:
            for src, dest in self.config.new_file.items():
                logger.debug(f"必须包含文件{src}，其将被拷贝至{dest}。")
        
        if not self.config.alter_file:
            logger.debug("不允许学生更改文件。")
        else:
            for _, dest in self.config.alter_file.items():
                logger.debug(f"要求学生更改文件{dest}。")
        
        if self.config.default_handler['operation'] == "ignore":
            logger.debug("所有其他文件将被忽略。")
        else:
            logger.warning(f"不被支持的默认操作：{self.config.default_handler['operation']}")
        
        logger.debug("")
        logger.debug("==================== 代码查重配置 ====================")
        logger.debug("")
        
        if not self.config.plagiarism_test:
            logger.debug("不进行代码查重检测。")
        else:
            logger.debug(f"使用id{self.config.moss_userid}进行MOSS查重。")
            for to_check, c_conf in self.config.plagiarism_test.items():
                logger.debug(f"对文件{to_check}进行代码查重：")
                if not c_conf.template:
                    logger.debug(f"\t无模板文件。")
                else:
                    logger.debug(f"\t提供模板文件{c_conf.template}。")
                for sol in c_conf.known_solutions:
                    logger.debug(f"\t已知解答{sol}。")
        
        logger.debug("")
        logger.debug("==================== 评测环境配置 ====================")
        logger.debug("")

        logger.debug(f"评测仓库地址: {self.config.repo}")
        logger.debug(f"评测仓库分支: {self.config.branch}")
        logger.debug(f"单个评测脚本: {self.config.test_script}")
        logger.debug(f"并行评测数量: {self.parallel_count}")
        logger.debug(f"待测文件位置: {self.stu_files_folder}")
        if self.config.overrides:
            logger.debug(f"覆写评测环境：")
            for override in self.config.overrides:
                if override.operation.type == "alteration":
                    logger.debug(f"\t替换文件{override.file_path}内容：")
                    logger.debug(f"\t\t原串：{override.operation.original}")
                    logger.debug(f"\t\t新串：{override.operation.altered}")
                elif override.operation.type == "creation":
                    logger.debug(f"\t创建文件{override.file_path}：")
                    logger.debug(f"\t\t内容：{override.operation.content}")
                else:
                    logger.error(f"\t在处理{override.file_path}时，遇到不支持的覆写方式{override.operation}。")
                    exit(0)


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="通用自动评测脚本。")
    arg_parser.add_argument("config", type=str, help="自动评测脚本配置文件。")
    arg_parser.add_argument("--parallel", "-p", type=int, default=1, help="并行任务数量。默认为1。")
    arg_parser.add_argument("--student-files", "-f", type=str, default="student_files", help="学生文件压缩包所在的文件夹。默认位于./student_files")
    arg_parser.add_argument('-v', '--verbose', action='count', default=0, help="输出等级。v越多，输出越多。支持-v -vv -vvv和空。")
    arg_parser.add_argument("--output-dir", "-o", type=str, default="result", help="评测得分输出文件夹。默认位于./grading_envs")
    arg_parser.add_argument("--codex", "-c", type=str, default="GB18030", help="输出.csv文件的编码。默认为GB18030。")
    arg_parser.add_argument("--plagiarism-threshold", "-t", type=int, default=90, help="抄袭判定阈值。默认为90。")
    arg_parser.add_argument("--anonymous", "-a", action="store_true", default=90, help="抄袭判定阈值。默认为90。")
    args = arg_parser.parse_args()
    
    logging.addLevelName(logging.DEBUG      , "细节")
    logging.addLevelName(LESSDEBUG_LOG_LEVEL, "调试")
    logging.addLevelName(logging.INFO       , "信息")
    logging.addLevelName(logging.WARNING    , "警告")
    logging.addLevelName(logging.ERROR      , "错误")
    logging.addLevelName(logging.FATAL      , "致命")
    logging.Logger.verbose = logging.Logger.debug
    logging.Logger.debug = less_debug

    if args.verbose == 0:
        logger.setLevel(logging.WARNING)
        ch.setLevel(logging.WARNING)
    elif args.verbose == 1:
        logger.setLevel(logging.INFO)
        ch.setLevel(logging.INFO)
    elif args.verbose == 2:
        logger.setLevel(LESSDEBUG_LOG_LEVEL)
        ch.setLevel(LESSDEBUG_LOG_LEVEL)
    else:
        logger.setLevel(logging.DEBUG)
        ch.setLevel(logging.DEBUG)
    
    ch.setFormatter(CustomFormatter())
    logger.addHandler(ch)

    grader = Grader(args)
    grader.setup_env()
    grader.batch_grade()
    grader.plagiarism_test()
    grader.visualize_plagiarism()

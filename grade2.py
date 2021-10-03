#!/bin/env python

from genericpath import isfile
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
from types import GeneratorType
import zipfile
import csv
import mosspy

logging.NOTICE = 25

ext_map = {
    "c"             : "c",
    "cc"            : "cc",
    "java"          : "java",
    "ml"            : "ml",
    "pascal"        : "pascal",
    "ada"           : "ada",
    "lisp"          : "lisp",
    "scheme"        : "scheme",
    "haskell"       : "haskell",
    "fortran"       : "fortran",
    "ascii"         : "ascii",
    "vhdl"          : "vhdl",
    "verilog"       : "verilog",
    "perl"          : "perl",
    "matlab"        : "matlab",
    "python"        : "python",
    "mips"          : "mips",
    "prolog"        : "prolog",
    "spice"         : "spice",
    "vb"            : "vb",
    "csharp"        : "csharp",
    "modula2"       : "modula2",
    "a8086"         : "a8086",
    "javascript"    : "javascript",
    "plsql"         : "plsql",
    "c++"           : "cc",
    "cpp"           : "cc",
    "h"             : "c",
    "hpp"           : "cc",
    "v"             : "verilog",
    "py"            : "python",
    "m"             : "matlab",
    "cs"            : "csharp",
    "js"            : "javascript"
}

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
        logging.DEBUG   : grey      + format + reset,
        logging.INFO    : white     + format + reset,
        logging.NOTICE  : green     + format + reset,
        logging.WARNING : yellow    + format + reset,
        logging.ERROR   : red       + format + reset,
        logging.CRITICAL: bold_red  + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


class DotDict(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


class FailedToCloneEnv(Exception):
    """无法下载评测环境。"""


class NoEnvAvailable(Exception):
    """找不到空闲的评测环境。"""


class PatternNotFound(Exception):
    def __init__(self, bad_file, bad_pattern):
        self.bad_file = bad_file
        self.bad_pattern = bad_pattern
    def __str__(self):
        return f"文件{self.bad_file}中未找到需要替换的字串{self.bad_pattern}。"


class BadFileNameFormat(Exception):
    def __init__(self, value):
        self.bad_file_name = value
    def __str__(self):
        return f"文件{self.bad_file_name}的文件名格式错误。"


class SrcFilesNotExist(Exception):
    def __init__(self, value):
        self.bad_file_name = value
    def __str__(self):
        return f"要拷贝的源文件/目录{self.bad_file_name}不存在。"


class BadArchiveFormat(Exception):
    def __init__(self, value):
        self.bad_file_name = value
    def __str__(self):
        return f"压缩文件{self.bad_file_name}格式错误或不支持。"


class Grader:
    def __init__(self, cli_configs, logger=None):
        self.logger = logger
        logger.notice("正在初始化批量评测脚本……")

        logger.debug("正在加载默认配置……")

        self.no_clone = cli_configs.no_clone
        self.no_judge = cli_configs.no_judge
        self.no_moss = cli_configs.no_moss
        self.parallel_count = cli_configs.parallel
        self.base_dir = path.dirname(path.realpath(__file__))
        self.config = DotDict({})
        self.fill_default_configs()
        
        logger.debug("默认配置加载完成。")
        
        logger.debug("开始加载配置文件……")
        logger.debug(f"评测脚本目录地址：{self.base_dir}")
        try:
            self.parse_config_file(cli_configs.config)
        except Exception as e:
            logger.fatal(f"加载配置文件时出错：{e}")
            exit(0)
        
        logger.debug(f"配置文件加载完成。")

        logger.debug(f"正在从命令行参数覆写配置……")
        self.config.parallel = cli_configs.parallel
        self.set_if_exist("student_files", cli_configs)
        self.config.student_files = self.concat_path(self.config.student_files)
        self.set_if_exist("output_dir", cli_configs)
        self.config.output_dir = self.concat_path(self.config.output_dir)
        self.config.output_score = self.concat_path("score.csv", self.config.output_dir)
        self.config.output_bad_file = self.concat_path("bad_file.csv", self.config.output_dir)
        self.config.output_logs_dir = self.concat_path("logs", self.config.output_dir)
        self.config.output_moss_reports = self.concat_path("moss_reports", self.config.output_dir)
        self.config.output_moss_visualize = self.concat_path("visualize.svg", self.config.output_dir)
        self.set_if_exist("cache_dir", cli_configs)
        self.config.cache_dir = self.concat_path(self.config.cache_dir)
        self.config.clean_repo = self.concat_path("clean_repo", self.config.cache_dir)
        self.config.env_root = self.concat_path("env_root", self.config.cache_dir)
        self.config.moss_files = self.concat_path("moss_files", self.config.cache_dir)
        self.set_if_exist("codex", cli_configs)
        self.set_if_exist("moss_userid", cli_configs)
        self.set_if_exist("repository", cli_configs)
        self.set_if_exist("branch", cli_configs)

        if isinstance(self.config.moss_userid, str):
            self.config.moss_userid = int(self.config.moss_userid)
        
        logger.debug(f"覆写配置完成。")

        logger.info(f"配置初始化完成，内容如下：")
        self.explain_config(logging.INFO)

        self.semaphore = Semaphore(self.parallel_count)
        self.env_available = [Lock() for _ in range(0, self.parallel_count)]
        self.result_mutex = Lock()
        self.output_mutex = Lock()
        self.results = []
        self.bad_files = []
        self.report_urls = {}
        
        logger.notice(f"批量评测脚本初始化完成。")


    def batch_grade(self):
        logger = self.logger

        if self.no_judge:
            logger.warning(f"检测到--no-judge，跳过批量评测阶段。")
        
        if not path.exists(self.config.student_files) or not os.listdir(self.config.student_files):
            logger.warning(f"检测到学生提交文件夹为不存在或为空。跳过批量评测阶段。")
            return
        
        logger.notice(f"开始构造批量评测环境……")
        try:
            self.clone_repo()
        except Exception as e:
            logger.fatal(f"无法下载评测环境，因为'{e}'。")
            exit(0)
        logger.notice(f"开始执行批量评测……")
        threads = []
        student_files = [path.join(self.config.student_files, f) for f in os.listdir(self.config.student_files) if path.isfile(f)]
        for f in student_files:
            self.semaphore.acquire()
            logger.debug(f"检测到提交文件{f}，准备评测。")
            t = Thread(target=self.single_grade, args=(f, ))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        logger.notice("评测已全部完成")
        

    def plagiarism_test(self):
        if self.no_moss:
            logger.warning(f"检测到--no-moss，跳过代码查重阶段。")
            return
        
        if not path.exists(self.config.moss_files) or not os.listdir(self.config.moss_files):
            logger.warning(f"检测到moss提交文件夹为不存在或为空。跳过代码查重阶段。")
            return


    def visualize_plagiarism(self):
        if self.no_moss:
            logger.warning(f"检测到--no-moss，跳过查重结果可视化生成阶段。")
            return
        
        if not path.exists(self.config.output_moss_reports) or not os.listdir(self.config.output_moss_reports):
            logger.warning(f"检测到moss检测结果文件夹为不存在或为空。跳过可视化生成阶段。")
            return


    def single_grade(self, student_zip_path):
        logger = self.logger
        process_msg = []
        missing_files = []
        logger.debug("开始初始化评测环境……")

        try:
            stu_id, stu_name, _ = self.parse_name(student_zip_path)
            env_id = self.alloc_env()
            self.safe_copy()
        except BadFileNameFormat as e:
            err_msg = f"文件名解析失败，因为{e}。"
            logger.error(err_msg)
            process_msg.append(err_msg)
            self.save_bad_file(student_zip_path, process_msg)
            self.semaphore.release()
            exit(0)
        except NoEnvAvailable as e:
            err_msg = f"初始化评测环境失败，因为'{e}'。"
            logger.error(err_msg)
            process_msg.append(err_msg)
            self.save_result_and_exit(env_id, stu_id, stu_name, 0, process_msg)
        
        env_path = path.join(self.config.env_root, f"env{env_id}")
        env_judge_path = path.join(env_path, "judge")
        env_stu_path = path.join(env_path, "stu")

        logger.debug("正在清理环境……")
        self.clear_folder(env_path)
        logger.debug("已经完成环境清理。")

        logger.debug(f"正在将{student_zip_path}解压至{env_stu_path}……")
        self.extract_nested(student_zip_path, env_stu_path)
        logger.debug(f"已经将{student_zip_path}解压至{env_stu_path}。")

        logger.debug(f"正在将环境{self.config.clean_repo}拷贝至{env_judge_path}……")
        self.safe_copy(self.config.clean_repo, env_judge_path)
        logger.debug(f"已经将环境{self.config.clean_repo}拷贝至{env_judge_path}。")
        
        logger.info(f"已完成评测环境初始化。")
        
        logger.info(f"开始备份MOSS查重文件……")
        moss_cplist = dict([(f, f"{f}/{stu_id}_{stu_name}_{f}") for f, conf in self.config.file_list.items() if conf.plagiarism_test])
        missing_files = missing_files + [f for f in self.safe_batch_copy(moss_cplist, env_stu_path, self.config.moss_files) if f not in missing_files]
        logger.info(f"MOSS查重文件备份完成。")
        
        logger.info(f"开始保存学生解答至输出……")
        moss_cplist = dict([(f, conf.copy_to_output.format(
            stu_id=stu_id, 
            stu_name=stu_name, 
            env_id=env_id
        )) for f, conf in self.config.file_list.items() if conf.copy_to_output])
        missing_files = missing_files + [f for f in self.safe_batch_copy(moss_cplist, env_stu_path, self.config.output_dir) if f not in missing_files]
        logger.info(f"学生解答保存完成。")
        
        logger.info(f"开始替换代入学生解答……")
        moss_cplist = dict([(f, conf.copy_to_env.format(
            stu_id=stu_id, 
            stu_name=stu_name, 
            env_id=env_id
        )) for f, conf in self.config.file_list.items() if conf.copy_to_env])
        missing_files = missing_files + [f for f in self.safe_batch_copy(moss_cplist, env_stu_path, env_judge_path) if f not in missing_files]
        logger.info(f"学生解答代入完成。")

        if missing_files:
            logger.warning(f"在{stu_name}（{stu_id}）的提交中，缺少下列文件：{'；'.join(missing_files)}。")
        
        logger.info(f"正在根据配置最终覆写评测环境……")
        for override_item in self.config.overrides:
            to_override = path.join(env_judge_path, override_item.file_path)
            if override_item.operation.type == "alteration":
                try:
                    original_expanded = override_item.operation.original.format(env_id=env_id, stu_id=stu_id, stu_name=stu_name)
                    altered_expanded = override_item.operation.altered.format(env_id=env_id, stu_id=stu_id, stu_name=stu_name)
                    self.alternate_file(env_judge_path, override_item.file_path, original_expanded, altered_expanded)
                    logger.verbose(f"完成对{to_override}内容的替换。")
                except Exception as e:
                    logger.error(f"在执行{stu_name}（{stu_id}）的环境构建时出现错误：{e}。")
                    process_msg.append(f"替换{override_item.file_name}内容时出错：{e}")
            elif override_item.operation.type == "creation":
                try:
                    content_expanded = override_item.operation.content.format(env_id=env_id, stu_id=stu_id, stu_name=stu_name)
                    self.create_file(env_judge_path, override_item.file_path, content_expanded)
                    logger.verbose(f"完成对{to_override}内容的替换。")
                except Exception as e:
                    logger.error(f"在执行{stu_name}（{stu_id}）的环境构建时出现错误：{e}。")
                    process_msg.append(f"生成{override_item.file_name}时出错：{e}")
        logger.info("评测环境最终覆写完成。")
        
        logger.info(f"环境准备已完成，开始评测。")


    def alternate_file(self, env_root, file_path, original, altered):
        to_override = path.join(env_root, file_path)
        with open(to_override, "r") as rf:
            replaced_txt = rf.read()
        if not replaced_txt.find(original):
            raise PatternNotFound(file_path, original)
        replaced_txt = replaced_txt.replace(original, altered)
        with open(to_override, "w") as wf:
            wf.write(replaced_txt)
    

    def create_file(self, env_root, file_path, content):
        to_create = path.join(env_root, file_path)
        with open(to_create, "w") as wf:
            wf.write(content)
    

    def safe_batch_copy(self, src_dst_pairs, src_root, dst_root):
        missing_files = []
        for src_file, dst_path in src_dst_pairs.items():
            try:
                self.safe_find_copy(src_file, src_root, path.join(dst_root, dst_path))
            except Exception as e:
                logger.info(f"无法拷贝，因为{e}。")
                missing_files.append(src_file)
    

    def safe_find_copy(self, file_name, src_dir, dst):
        matches = glob.glob(src_dir+"/**/"+file_name, recursive=True)
        if not matches:
            raise SrcFilesNotExist(file_name)
        
        if len(matches) != 1:
            logger.info(f"发现多个{file_name}文件，选择{matches[0]}。")
        logger.debug(f"在{src_dir}中找到文件{matches[0]}")
        self.safe_copy(matches[0], dst)

    
    def extract_nested(self, archive_path, dest_path):
        self.extract(archive_path, dest_path)
        archieves = glob.glob(dest_path + "/**/*.zip", recursive=True) + glob.glob(dest_path + "/**/*.rar", recursive=True)
        while archieves:
            logger.debug(f"在提交文件{archive_path}中发现嵌套压缩包{[path.basename(f) for f in archieves]}。正在重新解压。")
            for cf in archieves:
                self.extract(cf, dest_path)
                os.remove(cf)   # avoid infinite recursion


    def extract(self, archive_path, dest_path):
        logging.debug(f"将{archive_path}解压至{dest_path}。")
        try:
            if archive_path.endswith(".zip"):
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(dest_path)
                    return
            elif archive_path.endswith(".rar"):
                subprocess.check_call(["unrar", "x", archive_path, dest_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        except Exception as e:
            raise BadArchiveFormat(archive_path)
        raise BadArchiveFormat(archive_path)


    def alloc_env(self):
        for i, lock in enumerate(self.env_available):
            if lock.acquire(blocking=False):
                return i
        raise NoEnvAvailable


    def free_env(self, env_id):
        self.env_available[env_id].release()

    
    def save_result_and_exit(self, env_id, stu_name, stu_id, score, process_msg):
        logger = self.logger
        self.output_mutex.acquire()
        logger.notice(f"对{stu_name}（{stu_id}）的评测已执行完成，得分为{score}。")
        if process_msg:
            logger.warning(f"在上述评测执行过程中曾出现问题：{process_msg}。")
        self.output_mutex.release()
        self.result_mutex.acquire()
        self.results.append((stu_name, stu_id, score, process_msg))
        self.result_mutex.release()
        self.free_env(env_id)
        self.semaphore.release()
        exit(0)


    def save_bad_file(self, file_name, process_msg):
        logger = self.logger
        self.output_mutex.acquire()
        logger.warning(f"文件'{path.basename(file_name)}'不符合要求，因为'{process_msg}'。")
        logger.warning(f"所有不符合要求的文件都将被存储到{self.config.output_bad_file}中。")
        self.output_mutex.release()
        self.result_mutex.acquire()
        self.bad_files.append((file_name, process_msg))
        self.result_mutex.release()


    def parse_name(self, file_name):
        parse_regex = r"^([a-zA-Z0-9]{4,12})_([\w\u4e00-\u9fa5\u2000-\u206F]{2,30})_(.*)$"
        match_res = re.match(parse_regex, path.basename(file_name))
        if not match_res:
            raise BadFileNameFormat(path.basename(file_name))
        return match_res.groups()


    def clone_repo(self):
        logger = self.logger
        if path.exists(self.config.clean_repo) and os.listdir(self.config.clean_repo) and self.no_clone:
            logger.warning(f"检测到--no-clone标签，缓存合法，跳过评测环境下载。")
            return
        if self.no_clone:
            logger.warning(f"检测到--no-clone标签，但是缓存不合法，正在重新下载。")
        logger.debug(f"正在清空缓存文件夹……")
        self.clear_folder(self.config.clean_repo)
        logger.info(f"开始下载……")
        try:
            subprocess.check_output(["git", "clone", "--recursive", "-b", self.config.branch, self.config.repository, self.config.clean_repo])
        except subprocess.CalledProcessError:
            raise FailedToCloneEnv
        logger.info(f"评测环境已下载至{self.config.clean_repo}。")


    def set_if_exist(self, name, cli_configs):
        if vars(cli_configs)[name]:
            self.config[name] = vars(cli_configs)[name]


    def concat_path(self, append, base=None):
        if not base:
            base = self.base_dir
        return append if path.isabs(append) else path.join(base, append)


    def parse_config_file(self, file_position):
        logger = self.logger
        logger.info(f"正在加载配置文件……")
        loaded_config_dir = path.dirname(path.realpath(file_position))
        with open(file_position, "r") as config_file:
            loaded_config = DotDict(json.loads(config_file.read()))
        for file in loaded_config.file_list:
            conf = DotDict(loaded_config.file_list[file])
            if conf.plagiarism_test:
                conf.plagiarism_test = DotDict(conf.plagiarism_test)
                if conf.plagiarism_test.template:
                    conf.plagiarism_test.template = self.concat_path(conf.plagiarism_test.template, loaded_config_dir)
                if conf.plagiarism_test.known_solutions:
                    conf.plagiarism_test.known_solutions = [self.concat_path(f, loaded_config_dir) for f in conf.plagiarism_test.known_solutions]
                
                if not conf.plagiarism_test.language:
                    ext = file.split(".")[-1]
                    logger.warning(f"待查重文件{file}未指定语言，尝试从后缀名推断。")
                    conf.plagiarism_test.language = ext
                if conf.plagiarism_test.language not in ext_map:
                    logger.error(f"不受支持的语言：{conf.plagiarism_test.language}。取消对{file}的查重操作。")
                    file.plagiarism_test = None
                    continue
                conf.plagiarism_test.language = ext_map[conf.plagiarism_test.language]
                
            loaded_config.file_list[file] = conf
        loaded_config.overrides = [DotDict(o) for o in loaded_config.overrides]
        for o in loaded_config.overrides:
            o.operation = DotDict(o.operation)
            if o.operation.type != "alteration" and o.operation.type != "creation":
                logger.error(f"不支持该评测环境文件{o.file_path}的覆盖操作子类型'{o.operation.type}'。该操作将被撤销。")
        loaded_config.overrides = [o for o in loaded_config.overrides if o.operation.type == "alteration" or o.operation.type == "creation"]
        
        if loaded_config.output_dir:
            loaded_config.output_dir = self.concat_path(loaded_config.output_dir)
        if loaded_config.student_files:
            loaded_config.student_files = self.concat_path(loaded_config.student_files)
        if loaded_config.cache_dir:
            loaded_config.cache_dir = self.concat_path(loaded_config.cache_dir)

        for name, conf in loaded_config.items():
            self.config[name] = conf
    

    def safe_copy(self, src, dst):
        logging.debug(f"将{src}拷贝至{dst}。")
        if not path.isabs(src):
            src = self.concat_path(src)
        if not path.isabs(dst):
            dst = self.concat_path(dst)
        if not path.exists(src):
            raise SrcFilesNotExist(src)
        os.makedirs(path.dirname(dst), exist_ok=True)
        shutil.copytree(src, dst)
    

    def clear_folder(self, dir):
        shutil.rmtree(dir, ignore_errors=True)
        os.makedirs(dir, exist_ok=True)
    

    def fill_default_configs(self):
        self.config.codex = "GB18030"
        self.config.plagiarism_threshold = 90
        self.config.anonymous = False
        self.config.output_dir = self.concat_path("result")
        self.config.student_files = self.concat_path("student_files")
        self.config.cache_dir = self.concat_path("cache")
    

    def explain_config(self, log_level):
        logger = self.logger

        logger.log(log_level, "")
        logger.log(log_level, "==================== 通用评测配置 ====================")
        logger.log(log_level, "")
        
        logger.log(log_level, f"并行评测数量: {self.parallel_count}")
        logger.log(log_level, f"待测文件位置: {self.config.student_files}")
        if self.config.moss_userid:
            logger.log(log_level, f"MOSS用户id: {self.config.moss_userid}")
        if self.config.plagiarism_threshold:
            logger.log(log_level, f"查重报告阈值: {self.config.plagiarism_threshold}")
        logger.log(log_level, f"生成匿名查重结果: {'是' if self.config.anonymous else '否'}")
        if self.config.student_files:
            logger.log(log_level, f"待测文件位置: {self.config.student_files}")
        if self.config.output_dir:
            logger.log(log_level, f"输出目录: {self.config.output_dir}")
        if self.config.cache_dir:
            logger.log(log_level, f"缓存目录: {self.config.cache_dir}")

        logger.log(log_level, "")
        logger.log(log_level, "==================== 提交文件配置 ====================")
        logger.log(log_level, "")

        for file, conf in self.config.file_list.items():
            logger.log(log_level, f"提交文件{file}配置：")
            if conf.copy_to_env:
                chn_str = conf.copy_to_env.format(stu_name="{学生姓名}", stu_id="{学生id}", env_id="{评测环境编号}")
                logger.log(log_level, f"\t拷贝至评测环境/{chn_str}处。")
            if conf.copy_to_output:
                chn_str = conf.copy_to_output.format(stu_name="{学生姓名}", stu_id="{学生id}")
                logger.log(log_level, f"\t拷贝至输出文件夹/{chn_str}处。")
            if conf.plagiarism_test:
                logger.log(log_level, f"\t代码查重设置：")
                logger.log(log_level, f"\t\t编程语言：{conf.plagiarism_test.language}")
                if conf.plagiarism_test.template:
                    logger.log(log_level, f"\t\t模板文件：{conf.plagiarism_test.template}")
                if conf.plagiarism_test.known_solutions:
                    for sol in conf.plagiarism_test.known_solutions:
                        logger.log(log_level, f"\t\t已知解答：{sol}")
        
        logger.log(log_level, "")
        logger.log(log_level, "==================== 评测环境配置 ====================")
        logger.log(log_level, "")

        logger.log(log_level, f"评测仓库地址: {self.config.repository}")
        logger.log(log_level, f"评测仓库分支: {self.config.branch}")
        logger.log(log_level, f"评测命令: ")
        for cmd in self.config.test_script:
            logger.log(log_level, "\t" + " ".join(cmd).format(stu_name="{学生姓名}", stu_id="{学生id}", env_id="{评测环境编号}"))
        if self.config.overrides:
            logger.log(log_level, f"覆写评测环境：")
            for override in self.config.overrides:
                if override.operation.type == "alteration":
                    logger.log(log_level, f"\t替换文件{override.file_path}内容：")
                    logger.log(log_level, f"\t\t原串：{override.operation.original}")
                    logger.log(log_level, f"\t\t新串：{override.operation.altered}")
                elif override.operation.type == "creation":
                    logger.log(log_level, f"\t创建文件{override.file_path}：")
                    logger.log(log_level, f"\t\t内容：{override.operation.content}")
                else:
                    logger.log(log_level, f"\t在处理{override.file_path}时，遇到不支持的覆写方式{override.operation.type}。")
    

if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="通用自动评测脚本。脚本执行参数会覆盖配置文件参数。")
    arg_parser.add_argument("config", type=str, help="自动评测脚本配置文件。")
    arg_parser.add_argument("--parallel", "-p", type=int, default=1, help="并行任务数量。默认为1。")
    arg_parser.add_argument('-v', '--verbose', action='count', default=3, help="输出等级。v越多，输出越多，最多四个。")
    arg_parser.add_argument("--codex", "-c", type=str, default=None, help="输出.csv文件的编码。默认为GB18030。")
    arg_parser.add_argument("--moss-userid", type=str, default=None, help="MOSS评测系统的用户ID。")
    arg_parser.add_argument("--plagiarism-threshold", "-t", type=int, default=None, help="抄袭判定阈值。默认为90。")
    arg_parser.add_argument("--anonymous", "-a", action="store_true", default=False, help="将生成的可视化结果匿名化。")
    arg_parser.add_argument("--no-clone", action="store_true", default=False, help="跳过下载评测环境，使用缓存内容。")
    arg_parser.add_argument("--no-judge", action="store_true", default=False, help="跳过评测，使用缓存内容进行查重。")
    arg_parser.add_argument("--no-moss", action="store_true", default=False, help="跳过代码查重。")
    arg_parser.add_argument("--repository", "-r", type=str, default=None, help="评测环境Repo。支持本地文件与远程git链接。")
    arg_parser.add_argument("--branch", "-b", type=str, default=None, help="评测环境所在的Git分支。")
    arg_parser.add_argument("--student-files", "-f", type=str, default=None, help="学生文件压缩包所在的文件夹。默认位于./student_files。")
    arg_parser.add_argument("--output-dir", "-o", type=str, default=None, help="默认输出文件夹，内部包含score.csv和moss_report。默认为./result。")
    arg_parser.add_argument("--cache-dir", type=str, default=None, help="缓存文件夹。默认位于./cache。")

    args = arg_parser.parse_args()
    
    ch = logging.StreamHandler()
    logger = logging.getLogger()
    
    logging.addLevelName(logging.DEBUG      , "调试")
    logging.addLevelName(logging.INFO       , "信息")
    logging.addLevelName(logging.NOTICE     , "通知")
    logging.addLevelName(logging.WARNING    , "警告")
    logging.addLevelName(logging.ERROR      , "错误")
    logging.addLevelName(logging.FATAL      , "致命")

    logging_level = logging.DEBUG
    if args.verbose == 0:
        logging_level = logging.ERROR
    elif args.verbose == 1:
        logging_level = logging.WARNING
    elif args.verbose == 2:
        logging_level = logging.NOTICE
    elif args.verbose == 3:
        logging_level = logging.INFO
    
    ch.setLevel(logging_level)
    logger.setLevel(logging_level)

    ch.setFormatter(CustomFormatter())
    logger.addHandler(ch)
    
    def notice(self, message, *args, **kws):
        if self.isEnabledFor(logging.NOTICE):
            self._log(logging.NOTICE, message, args, **kws) 
    logging.Logger.notice = notice
    g = Grader(args, logger)
    g.batch_grade()
    g.plagiarism_test()
    g.visualize_plagiarism()
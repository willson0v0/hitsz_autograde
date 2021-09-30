# 批量评测脚本使用说明

## 命令行参数

参数                                              | 含义
--------------------------------------------------|------------------------------------------------------
`-h`                                              | 输出帮助   
`--parallel PARALLEL, -p PARALLEL`                | 指定并行任务数量
`--student-files STUDENT_FILES, -f STUDENT_FILES` | 学生文件压缩包所在的文件夹。默认位于./student_files
`-v, --verbose`                                   | 输出等级。v越多，输出越多。支持-v -vv -vvv。
`--output-dir OUTPUT_DIR, -o OUTPUT_DIR`          | 评测得分输出文件夹。默认位于./grading_envs
`--codex CODEX, -c CODEX`                         | 输出.csv文件的编码。默认为GB2312。
config                                            | json配置文件，格式见下

## 配置文件格式

```json
{
    // 要求学生创建的文件
    "new_file": {
        "上交的文件名1": "相对于评测环境根目录的相对地址",
        "上交的文件名2": "相对于评测环境根目录的相对地址",
        "上交的文件名3": "相对于评测环境根目录的相对地址"
    },
    // 要求学生修改的文件
    "alter_file": { 
        "上交的文件名1": "相对于评测环境根目录的相对地址",
        "上交的文件名2": "相对于评测环境根目录的相对地址",
        "上交的文件名3": "相对于评测环境根目录的相对地址"
    },
    // 对其他文件的默认操作。目前只支持ignore，即无视。
    "default_handler": {
        "operation": "ignore"
    },
    "plagiarism_test": {
        "上交的文件名1": {
            "template": "模板文件位置，相对于配置文件的相对路径或绝对路径；可以留空，代表没有模板。",
            "known_solutions": [
                "已知的解法1，比如CSDN上的文章，相对于配置文件的相对路径或绝对路径",
                "已知的解法2，比如Github上的文件，相对于配置文件的相对路径或绝对路径",
                "已知的解法3，比如某篇博客里的文章，相对于配置文件的相对路径或绝对路径",
                "已知的解法4，比如往年的答案，相对于配置文件的相对路径或绝对路径",
            ]
        },
        "上交的文件名2": {
            "template": "模板文件位置，相对于配置文件的相对路径或绝对路径；可以留空，代表没有模板。",
            "known_solutions": [
                "已知的解法1，比如CSDN上的文章，相对于配置文件的相对路径或绝对路径",
                "已知的解法2，比如Github上的文件，相对于配置文件的相对路径或绝对路径",
                "已知的解法3，比如某篇博客里的文章，相对于配置文件的相对路径或绝对路径",
                "已知的解法4，比如往年的答案，相对于配置文件的相对路径或绝对路径",
            ]
        },
        "上交的文件名3": {
            "template": "模板文件位置，相对于配置文件的相对路径或绝对路径；可以留空，代表没有模板。",
            "known_solutions": [
                "已知的解法1，比如CSDN上的文章，相对于配置文件的相对路径或绝对路径",
                "已知的解法2，比如Github上的文件，相对于配置文件的相对路径或绝对路径",
                "已知的解法3，比如某篇博客里的文章，相对于配置文件的相对路径或绝对路径",
                "已知的解法4，比如往年的答案，相对于配置文件的相对路径或绝对路径",
            ]
        },
    },
    // 评测执行前，对评测文件夹里的内容进行覆写，以达到自定义配置的目的。
    // operation目前支持alteration（替换）和 creation（创建）两种。
    // 支持在字符串中使用{env_id}代表评测环境编号、使用{stu_id}代表学号、使用{name}代表姓名
    "overrides": [
        {
            "file_path": "需要被覆写的文件路径1，相对于评测环境根目录的相对地址",
            "operation": {
              "type": "alteration",
              "original": "需要被替换掉的片段",
              "altered": "需要作为替换被写入的片段"
            },
            "file_path": "需要被覆写的文件路径2，相对于评测环境根目录的相对地址",
            "operation": {
              "type": "creation",
              "content": "文件内容"
            },
        }
    ],
    // moss评测机的用户id
    "moss_userid": 507639744,
    // moss评测的输出路径，默认为moss_report
    "moss_report_dir": "moss_report",
    "test_script": "需要运行的子测试脚本",
    // 用于匹配输出结果的正则表达式。其中第一个分组会被提取并作为得分。以下表达式可以匹配形如"Score: 分数/100"的输出。
    "result_regex": "^Score: ([0-9]{1,3})/100$",
    // 评测环境的git repo。可以是本地文件夹，或者是远程url
    "repo": "/mnt/shared_resources/xv6-labs-2020",
    // 评测环境所在的分支。
    "branch": "util"
}
```
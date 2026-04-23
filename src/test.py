# import json

# from pyncm.apis.login import (
#     LoginViaCellphone,
#     SetSendRegisterVerifcationCodeViaCellphone,
#     GetRegisterVerifcationStatusViaCellphone,
# )
# from pyncm import GetCurrentSession, DumpSessionAsString
# from pprint import pprint


# def login():
#     import inquirer

#     query = inquirer.prompt(
#         [
#             inquirer.Text("phone", message="手机号"),
#             inquirer.Text("ctcode", message="国家代码(默认86)"),
#         ]
#     )
#     phone, ctcode = query["phone"], query["ctcode"] or 86
#     if inquirer.confirm("使用手机验证码登陆？"):
#         result = SetSendRegisterVerifcationCodeViaCellphone(phone, ctcode)
#         if not result.get("code", 0) == 200:
#             pprint(result)
#         else:
#             print("[-] 已发送验证码")
#         while True:
#             captcha = inquirer.text("输入验证码")
#             verified = GetRegisterVerifcationStatusViaCellphone(phone, captcha, ctcode)
#             pprint(verified)
            
#             if verified.get("code", 0) == 200:
#                 print("[-] 验证成功")
#                 break
#         result = LoginViaCellphone(phone, captcha=captcha, ctcode=ctcode)
#         pprint(result)
#         with open('res.json', 'w', encoding='utf-8') as f:
#             json.dump(result, f, indent=4)
#     else:
#         password = inquirer.password("输入密码")
#         result = LoginViaCellphone(phone, password=password, ctcode=ctcode)
#         pprint(result)
#     print("[!] 登录态 Session:", DumpSessionAsString(GetCurrentSession()))
#     print(
#         '[-] 此后可通过 SetCurrentSession(LoadSessionFromString("PYNCMe...")) 恢复当前登录态'
#     )
#     return True


# if __name__ == "__main__":
#     print("[-] 登录测试")
#     assert login(), "登陆失败"

# import json
# from pprint import pprint

# from pyncm import apis
# import pyncm

# with open('res2.json', 'w', encoding='utf-8') as f:
#     json.dump(apis.login.LoginViaAnonymousAccount(), f, indent=4)
#     print(pyncm.DumpSessionAsString(pyncm.GetCurrentSession()))

# import subprocess

# output = subprocess.check_output(['uv', 'pip', 'freeze']).decode()
# libs = output.splitlines()

# result = [lib.split('==')[0] for lib in libs]
# print(f'found {len(result)} libs')

# for i, lib in enumerate(result):
#     print(f'upgrading {i + 1}/{len(result)} ({lib})')
#     subprocess.run(['uv', 'pip', 'install', '--upgrade', lib])

import subprocess
import json
import os
import sys
import re
from datetime import datetime
from pathlib import Path

def run_git_command(cmd):
    """执行git命令并返回输出，修复Windows编码问题"""
    try:
        # 设置环境变量强制使用UTF-8编码
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['LANG'] = 'en_US.UTF-8'
        
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            encoding='utf-8',  # 明确指定UTF-8编码
            errors='replace',   # 替换无法解码的字符
            env=env,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"执行命令失败: {cmd}")
        print(f"错误信息: {e.stderr}")
        return None
    except Exception as e:
        print(f"执行命令时发生异常: {e}")
        return None

def get_all_commits():
    """获取所有提交的基本信息"""
    # 获取提交列表：hash, author name, date, subject
    cmd = 'git log --all --pretty=format:"%H|%an|%ad|%s" --date=iso'
    output = run_git_command(cmd)
    
    if not output:
        print("警告: 没有获取到提交记录")
        return []
    
    commits = []
    for line in output.strip().split('\n'):
        if line and '|' in line:
            parts = line.split('|', 3)  # 最多分割3次
            if len(parts) == 4:
                commit = {
                    'hash': parts[0],
                    'author': parts[1],
                    'date': parts[2],
                    'message': parts[3]
                }
                commits.append(commit)
    
    return commits

def parse_diff(diff_text):
    """解析diff文本，提取文件变更和具体内容"""
    if not diff_text:
        return []
    
    changes = []
    current_file = None
    file_changes = []
    
    lines = diff_text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 检测文件变更标记 (diff --git a/file b/file)
        if line.startswith('diff --git'):
            # 保存上一个文件
            if current_file:
                changes.append({
                    'file': current_file,
                    'changes': file_changes
                })
            
            # 提取文件名
            match = re.search(r'diff --git a/(.+?) b/(.+)$', line)
            if match:
                current_file = match.group(1)
            else:
                current_file = "unknown"
            file_changes = []
            
            # 跳过后面的 --- 和 +++ 行
            i += 2
        
        # 检测变更标记 (+, -, 空格)
        elif line and (line[0] in ['+', '-', ' ']) and len(line) > 0:
            # 只记录实际的文件内容变更，跳过@@行
            if not line.startswith('@@'):
                # 限制每行长度，避免过长
                if len(line) > 200:
                    file_changes.append(line[:200] + "...")
                else:
                    file_changes.append(line)
        
        i += 1
    
    # 保存最后一个文件
    if current_file:
        changes.append({
            'file': current_file,
            'changes': file_changes
        })
    
    return changes

def get_commit_files(commit_hash):
    """获取提交中变更的文件列表和状态"""
    # 获取文件状态 (A: added, M: modified, D: deleted, R: renamed)
    cmd = f'git show --name-status --format="" {commit_hash}'
    output = run_git_command(cmd)
    
    files = []
    if output:
        for line in output.strip().split('\n'):
            if line:
                parts = line.split('\t')
                if len(parts) >= 2:
                    status = parts[0]
                    filename = parts[1]
                    files.append({'status': status, 'file': filename})
    
    return files

def get_commit_diff(commit_hash):
    """获取提交的diff内容"""
    cmd = f'git show {commit_hash}'
    output = run_git_command(cmd)
    return output or ""

def save_to_custom_format(commits_data, output_file):
    """按照自定义格式保存到txt文件"""
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            # 写入头部信息
            f.write("=" * 80 + "\n")
            f.write(f"Git仓库提交记录导出\n")
            f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"提交总数: {len(commits_data)}\n")
            f.write("=" * 80 + "\n\n")
            
            for i, commit in enumerate(commits_data, 1):
                # 写入提交头部
                short_hash = commit['hash'][:8]
                f.write(f"({short_hash}) By {commit['author']}\n")
                
                # 写入提交消息（多行）
                message_lines = commit['message'].split('\n')
                for msg_line in message_lines:
                    if msg_line.strip():
                        f.write(f"{msg_line}\n")
                
                # 获取文件变更列表和diff内容
                files = get_commit_files(commit['hash'])
                diff_text = get_commit_diff(commit['hash'])
                
                # 解析diff内容
                parsed_diff = parse_diff(diff_text)
                
                # 如果没有解析到详细diff，使用name-status作为备用
                if not parsed_diff and files:
                    for file_info in files:
                        status_map = {
                            'A': 'A',  # Added
                            'M': 'M',  # Modified
                            'D': 'D',  # Deleted
                            'R': 'R'   # Renamed
                        }
                        status = status_map.get(file_info['status'][0], '?')
                        f.write(f"{status} {file_info['file']}\n")
                else:
                    # 写入详细的文件变更
                    for file_change in parsed_diff:
                        # 确定文件状态
                        status = 'M'  # 默认为修改
                        if file_change['changes']:
                            # 检查是否有新增或删除的标记
                            has_additions = any(line.startswith('+') for line in file_change['changes'])
                            has_deletions = any(line.startswith('-') for line in file_change['changes'])
                            
                            if has_additions and not has_deletions:
                                status = 'A'  # 新增文件（所有行都是+）
                            elif has_deletions and not has_additions:
                                status = 'D'  # 删除文件（所有行都是-）
                            else:
                                status = 'M'  # 修改文件
                        
                        # 写入文件名
                        f.write(f"{status} {file_change['file']}\n")
                        
                        # 写入具体的变更内容
                        for change_line in file_change['changes']:
                            # 限制行长度
                            if len(change_line) > 200:
                                change_line = change_line[:200] + "..."
                            f.write(f"{change_line}\n")
                
                # 添加分隔线（除了最后一个提交）
                if i < len(commits_data):
                    f.write("\n" + "-" * 80 + "\n\n")
        
        print(f"✓ 文本报告已保存到: {output_file}")
        print(f"  文件大小: {os.path.getsize(output_file)} 字节")
        return True
    except Exception as e:
        print(f"✗ 保存文本文件失败: {e}")
        return False

def save_to_json(commits_data, output_file):
    """保存数据到JSON文件"""
    try:
        # 准备JSON数据（包含所有信息）
        json_data = []
        for commit in commits_data:
            files = get_commit_files(commit['hash'])
            diff_text = get_commit_diff(commit['hash'])
            
            json_data.append({
                'hash': commit['hash'],
                'author': commit['author'],
                'date': commit['date'],
                'message': commit['message'],
                'files': files,
                'diff': diff_text
            })
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        print(f"✓ JSON数据已保存到: {output_file}")
        print(f"  文件大小: {os.path.getsize(output_file)} 字节")
        return True
    except Exception as e:
        print(f"✗ 保存JSON文件失败: {e}")
        return False

def main():
    # 检查是否在git仓库中
    if not os.path.exists('.git'):
        print("错误: 当前目录不是Git仓库!")
        print(f"当前路径: {os.getcwd()}")
        sys.exit(1)
    
    print("正在获取Git仓库信息...")
    print(f"仓库路径: {os.getcwd()}")
    
    # 获取所有提交的基本信息
    print("正在获取提交列表...")
    commits = get_all_commits()
    
    if not commits:
        print("没有找到任何提交记录!")
        print("提示: 请确保当前目录是Git仓库并且有提交历史")
        sys.exit(1)
    
    print(f"找到 {len(commits)} 个提交")
    
    # 生成输出文件名
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    text_file = f"git_commits_export_{timestamp}.txt"
    json_file = f"git_commits_export_{timestamp}.json"
    
    # 保存为自定义文本格式
    print("正在导出为文本格式...")
    save_to_custom_format(commits, text_file)
    
    # 可选：同时保存JSON格式
    print("\n正在导出为JSON格式...")
    
    print("\n" + "=" * 50)
    print("导出完成!")
    print(f"文本文件: {text_file}")
    print("=" * 50)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n用户中断操作")
        sys.exit(0)
    except Exception as e:
        print(f"\n发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
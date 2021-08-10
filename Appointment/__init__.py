import os
import json
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
print(BASE_DIR)
class LongTermInfo():
    def __init__(self):
        # 读取json文件, 包括url地址、输入输出位置等
        try:
            load_file = open(os.path.join(BASE_DIR,"Appointment","load_setting.json"),'r')
        except:
            raise IOError("Can not found load_setting.json.")

        try:
            load_json = json.load(load_file)
            load_file.close()
            self.login_url = load_json["url"]['login_url']     # 由陈子维学长提供的统一登录入口
            self.img_url = load_json["url"]['img_url']         # 跳过DNS解析的秘密访问入口,帮助加速头像
            self.this_url = load_json["url"]['this_url']       # 跳过DNS解析的秘密访问入口,帮助加速头像
            self.wechat_url = load_json["url"]['wechat_url']   # 访问企业微信封装层的接口
            self.system_log = load_json["url"]['system_log']
        except:
            raise IndexError("Can not find necessary field, please check your json file.")

        # # 读取敏感密码参数
        # try:
        #     load_file = open(os.path.join("Appointment","token.json"),'r')
        # except:
        #     raise IOError("Can not found token.json. Please use local debug mode instead.")
        #
        # try:
        #     load_json = json.load(load_file)
        #     load_file.close()
        #     self.YPPF_salt = load_json['YPPF_salt']
        #     self.wechat_salt = load_json['wechat_salt']
        # except:
        #     raise IndexError("Can not find token field, please check your json file.")

        # 设置全局参数
        # added by wxy 人数检查
        # 修改这两个变量以决定检查的宽严
        self.check_rate = 0.6  # 摄像头发来的每个数据，都有check_rate的几率当成采样点
        self.camera_qualified_check_rate = 0.4  # 人数够的次数达到(总采样次数*rate)即可。
        # 由于最短预约时间为30分钟，允许晚到15分钟，所以达标线设在50%以下比较合适(?)
        
        # 是否清除一周前的预约
        self.delete_appoint_weekly = False

        # 表示当天预约时放宽的人数下限
        self.today_min = 2
        # 是否允许不存在学生自动注册
        self.allow_newstu_appoint = True

        # 是否开启登录系统，默认为开启
        self.debug_stuid = "1800017704" # YHT学号
        self.account_auth = True

        # end



global_info = LongTermInfo()
print("finish loading default setting.")

# Url加密相关
import hashlib
from django.contrib.auth.hashers import MD5PasswordHasher

# 哈希加密工具
class MyMD5PasswordHasher(MD5PasswordHasher):
    algorithm = "mymd5"
    salt = ""

    def __init__(self, salt):
        self.salt = salt

    def encode(self, password):
        assert password is not None
        password = (password+self.salt).encode('utf-8')
        hash = hashlib.md5(password).hexdigest().upper()
        return hash

    def verify(self, password, encoded):
        encoded_2 = self.encode(password)
        return encoded.upper() == encoded_2.upper()

hash_identity_coder = MyMD5PasswordHasher(salt='use_for_debug')#global_info.YPPF_salt)
hash_wechat_coder = MyMD5PasswordHasher(salt='use_for_debug')#(salt=global_info.wechat_salt)

print(hash_identity_coder.encode('123456'))
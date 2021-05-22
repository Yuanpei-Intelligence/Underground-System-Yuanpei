# store some funcitons

import requests as requests
import json
import hashlib
from django.contrib.auth.hashers import MD5PasswordHasher
import threading
from Appointment.models import Student, Room, Appoint  # 数据库模型
from django.db import transaction  # 原子化更改数据库
from datetime import datetime, timedelta
import os, time

ip_room_dict = {
    "152": "B104",
    "155": "B104",
    "132": "B106",
    "153": "B106",
    "131": "B107A",
    "135": "B107B",
    "134": "B108",
    "151": "B108",
    "146": "B111",
    "149": "B111",
    "141": "B112",
    "148": "B112",
    # "138": "B114", 不准 自习室
    "139": "B114",
    "144": "B118",
    "145": "B118",
    "140": "B119",
    "147": "B119",
    "129": "B205",
    "102": "B206",
    "106": "B206",
    "105": "B207",
    "107": "B207",
    "110": "B208",
    "111": "B208",
    "103": "B209",
    "108": "B209",
    "121": "B214",
    # "128": "B214", 镜子 舞蹈室
    "119": "B215",
    "117": "B216",
    # "124": "B216", 镜子 跑步机房
    "122": "B217",
    "126": "B217",
    "113": "B218",
    "120": "B220",
    "130": "B220",
    "112": "B221",  # 琴房 看不到门口谱子位置
    "123": "B221",  # 琴房 看不到小提琴位
    "118": "B222",
    "125": "B222",
}

door_room_dict = {
    "2020092016162884": "B104",
    "2020092016370963": "B106A",
    "2020092016422704": "B106B",
    "2020092016464351": "B107A",
    "2020092016550340": "B108A",
    "2020092017010542": "B108B",
    "2020092017070505": "B107B",
    "2020092017084647": "B000",  # 值班室
    "2020092017233640": "B112A",
    "2020092017234462": "B112B",
    "2020092017235201": "B111",
    "2020092017393941": "B114A",
    "2020092017475922": "B114B",
    "2020092017481264": "B118",
    "2020092017482150": "B119",
    "2020092018023538": "B218",
    "2020092018030345": "B220",
    "2020092018031303": "B221",
    "2020092018032470": "B222",
    "2020092018182960": "B214A",
    "2020092018184631": "B214B",
    "2020092018185928": "B216",
    "2020092018201454": "B217",
    "2020092018400410": "B209",
    "2020092018521223": "B205",
    "2020092018522586": "B206A",
    "2020092018523750": "B206B",
    "2020092018525770": "B208",
}

# 给定摄像头ip后三位，返回摄像头对应的Rid


def iptoroom(ip):
    return ip_room_dict[ip]


# 给定房间门牌号id，返回对应的Rid
def doortoroom(door):
    return door_room_dict[door]

############ modified by wxy ############


system_log = "Appoint_Sys"


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


# 给企业微信发送消息
# update 0309:原来是返回状态码和错误信息，现在在这个函数中直接做错误处理，如果处理不了就写日志，不返回什么了
hash_wechat_coder = MyMD5PasswordHasher(salt='')
wechat_post_url = ''  # 像企业微信发送消息的

send_message = requests.session()

def send_wechat_message(stu_list, starttime, room, message_type, major_student, usage, announcement, num, reason=''):#, credit=''):
    if message_type == 'new':
        message = '您有一条新的预约\n'  # 发起者 用途 人数
        message += '时间：'+starttime.strftime("%Y-%m-%d %H:%M")+'\n地点：'+str(room)
        message += '\n发起者：'+major_student+'\n用途：'+usage+'\n人数：'+str(num)
        if announcement:
            message += '\n预约通知：'+announcement
    elif message_type == 'start':
        message = '您有一条预约即将在15分钟后开始\n'  # 发起者 用途 人数
        message += '时间：'+starttime.strftime("%Y-%m-%d %H:%M")+'\n地点：'+str(room)
        message += '\n发起者：'+major_student+'\n用途：'+usage+'\n人数：'+str(num)
        if announcement:
            message += '\n预约通知：'+announcement
    elif message_type == 'new&start':
        message = '您有一条新的预约\n并即将在15分钟内开始'  # 发起者 用途 人数
        message += '\n时间：'+starttime.strftime("%Y-%m-%d %H:%M")+'\n地点：'+str(room)
        message += '\n发起者：'+major_student+'\n用途：'+usage+'\n人数：'+str(num)
        if announcement:
            message += '\n预约通知：'+announcement
    elif message_type == 'violated':
        message = '您有一条新增的违约记录'  # 原因
        message += '\n时间：'+starttime.strftime("%Y-%m-%d %H:%M")+'\n地点：'+str(room)
        message += '\n原因：'+reason#+'\n当前信用分：'+str(credit)
    elif message_type == 'cancel':
        message = '您有一条预约被取消'  # 发起者 用途 人数
        message += '\n时间：'+starttime.strftime("%Y-%m-%d %H:%M")+'\n地点：'+str(room)
        message += '\n发起者：'+major_student+'\n用途：'+usage+'\n人数：'+str(num)
    elif message_type == 'longterm':    # 发起一条长线预约
        message = '【管理员操作】您有一条预约新增了未来'+str(reason)+'周的同时段预约\n'  # 类型
        message += '时间：'+starttime.strftime("%Y-%m-%d %H:%M") + '\n地点：'+str(room)
        message += '\n发起者：'+major_student+'\n用途：'+usage+'\n人数：'+str(num)
        if announcement:
            message += '\n预约通知：'+announcement
    elif message_type == 'confirm_admin_w2c':    # WAITING to CONFIRMED
        message = '【管理员操作】您有一条预约已确认完成\n'  # 类型
        message += '时间：'+starttime.strftime("%Y-%m-%d %H:%M") + '\n地点：'+str(room)
        message += '\n用途：'+usage+'\n人数：'+str(num)
    elif message_type == 'confirm_admin_v2j':    # VIOLATED to JUDGED
        message = '【管理员操作】您有一条违约的预约申诉成功\n'  # 类型
        message += '时间：'+starttime.strftime("%Y-%m-%d %H:%M") + '\n地点：'+str(room)
        message += '\n用途：'+usage+'\n人数：'+str(num)
    elif message_type == 'violate_admin':    # VIOLATED
        message = '【管理员操作】您有一条预约被判定违约\n'  # 类型
        message += '时间：'+starttime.strftime("%Y-%m-%d %H:%M") + '\n地点：'+str(room)
        message += '\n用途：'+usage+'\n人数：'+str(num)+'\n如有疑问请联系管理员'
    else:
        # todo: 记得测试一下!为什么之前出问题的log就找不到呢TAT
        operation_writer(system_log,
                         starttime.strftime("%Y-%m-%d %H:%M:%S") + str(
                             room) + message_type + "出错，原因：unknown message_type", "func[send_wechat_message]",
                         "Problem")
        return

    secret = hash_wechat_coder.encode(message)
    post_data = {
        'touser': stu_list,
        'toall': True,
        'content': message,
        'secret': secret,
        'card':True
    }
    response = send_message.post(wechat_post_url, data=json.dumps(post_data))
    for _ in range(0, 3):  # 重发3次
        response = response.json()
        if response['status'] == 200:
            operation_writer(system_log,
                             starttime.strftime("%Y-%m-%d %H:%M:%S") + str(room) 
                             + message_type + "向微信发消息成功", "func[send_wechat_message]",
                             "OK")
            return
        # else check the reason, send wechat message again
        
        if response['data']['errMsg'] == '部分或全部发送失败':
            stu_list = [i[0] for i in response['data']['detail']]
            post_data = {
                'touser': stu_list,
                'toall': True,
                'content': message,
                'secret': secret,
                'card':True
            }
            response = send_message.post(
                '', data=json.dumps(post_data))
        elif response['data']['errMsg'] == '缺少参数或参数不符合规范':
            operation_writer(system_log,
                             starttime.strftime("%Y-%m-%d %H:%M:%S") + str(
                                 room) + message_type + "向微信发消息失败，原因：缺少参数或参数不符合规范", "func[send_wechat_message]",
                             "Problem")
            return
        elif response['data']['errMsg'] == '应用不在发送范围内':
            operation_writer(system_log,
                             starttime.strftime("%Y-%m-%d %H:%M:%S") + str(
                                 room) + message_type + "向微信发消息失败，原因：应用不在发送范围内", "func[send_wechat_message]",
                             "Problem")
            return
        time.sleep(1)
    # 重发3次都失败了
    operation_writer(system_log,
                     starttime.strftime("%Y-%m-%d %H:%M:%S") + str(room) + message_type +
                     "向微信发消息失败，原因：多次发送失败. 发起者为: " + str(major_student), "func[send_wechat_message]",
                     "Problem")
    return
    # return  1, response['data']['errMsg']


# 线程锁，用于对数据库扣分操作时的排他性
lock = threading.RLock()
# 信用分扣除体系
real_credit_point = True  # 如果为false 那么不把扣除信用分纳入范畴


def appoint_violate(input_appoint, reason):  # 将一个aid设为违约 并根据real_credit_point设置
    try:
        #lock.acquire()
        operation_succeed = False
        appoints = Appoint.objects.select_related('major_student').select_for_update().filter(Aid=input_appoint.Aid)
        with transaction.atomic():
            if len(appoints) != 1:
                raise AssertionError
            for appoint in appoints:    #按照假设，这里的访问应该是原子的，所以第二个程序到这里会卡主
                really_deduct = False

                if real_credit_point and appoint.Astatus != Appoint.Status.VIOLATED:  # 不出现负分；如果已经是violated了就不重复扣分了
                    if appoint.major_student.Scredit > 0:  # 这个时候需要扣分
                        appoint.major_student.Scredit -= 1
                        really_deduct = True
                    appoint.Astatus = Appoint.Status.VIOLATED
                    appoint.Areason = reason
                    appoint.save()
                    appoint.major_student.save()
                    operation_succeed = True

                    major_sid = str(appoint.major_student.Sid)
                    astart = appoint.Astart
                    aroom = str(appoint.Room)
                    major_name = str(appoint.major_student.Sname)
                    usage = str(appoint.Ausage)
                    announce = str(appoint.Aannouncement)
                    number = str(appoint.Ayp_num+appoint.Anon_yp_num)
                    status = str(appoint.get_status())
                    aid = str(appoint.Aid)
                    areason = str(appoint.get_Areason_display())
                    credit = str(appoint.major_student.Scredit)

        if operation_succeed: # 本任务执行成功
            send_wechat_message([major_sid],
                        astart,
                        aroom, 
                        "violated",
                        major_name,
                        usage,
                        announce,
                        number,
                        status,
                        #appoint.major_student.Scredit,
                        )  # totest: only main_student
            str_pid = str(os.getpid())
            operation_writer(major_sid, "预约" + str(aid) + "出现违约:" +
                        str(areason) + ";是否扣除信用分:"+str(really_deduct)+
                        ";剩余信用分"+str(credit), "func[appoint_violate]"+str_pid, "OK") #str(os.getpid()),str(threading.current_thread().name()))
            #lock.release()
        return True, ""
    except Exception as e:
        return False, "in func[appoint_violate]: " + str(e)


# 文件操作体系
log_root = "logstore"
if not os.path.exists(log_root):
    os.mkdir(log_root)
log_root_path = os.path.join(os.getcwd(), log_root)
log_user = "user_detail"
if not os.path.exists(os.path.join(log_root_path, log_user)):
    os.mkdir(os.path.join(log_root_path, log_user))
log_user_path = os.path.join(log_root_path, log_user)

# 每周定时删除预约的程序，用于减少系统内的预约数量


def write_before_delete(appoint_list):
    date = str(datetime.now().date())

    write_path = os.path.join(log_root_path, date+".log")
    log = open(write_path, mode="a")  # open file

    period_start = (datetime.now()-timedelta(days=7)).date()
    log.write(str(period_start) + "~" + str(date) + "\n")
    for appoint in appoint_list:
        if appoint.Astatus != Appoint.Status.CANCELED:  # not delete
            log.write(str(appoint.toJson()).encode(
                "gbk", 'ignore').decode("gbk", "ignore"))
            log.write("\n")

    log.write("end of file\n")
    log.close()


# 通用日志写入程序 写入时间(datetime.now()),操作主体(Sid),操作说明(Str),写入函数(Str)
# 参数说明：第一为Sid也是文件名，第二位消息，第三位来源的函数名（类别）
def operation_writer(user, message, source, status_code="OK"):
    lock.acquire()
    try:
        format_user = str(user).ljust(20)
        written_time = str(datetime.now()) + "   "
        source = str(source).ljust(30)
        message = written_time + format_user + \
            source + status_code.ljust(10) + message+"\n"
        
        file = open(os.path.join(log_user_path, str(user)+".log"), mode='a')
        file.write(message)
        file.close()
        if status_code == "Error":
            send_wechat_message(
                stu_list=['','',''],
                starttime=datetime.now(),
                room=Room.objects.get(Rid="B107A"),
                message_type="violated",
                major_student="地下室系统",
                usage="发生Error错误",
                announcement="",
                num=1,
                reason=message,
                # credit=appoint.major_student.Scredit,
            )
    except Exception as e:
        # 最好是发送邮件通知存在问题
        # 待补充
        print(e)
        
    lock.release()

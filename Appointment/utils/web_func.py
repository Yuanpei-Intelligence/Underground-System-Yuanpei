import requests as requests
from YPUnderground import global_info
from Appointment.models import Student, Room, Appoint, College_Announcement
from django.db.models import Q  # modified by wxy
from datetime import datetime, timedelta, timezone, time, date
import Appointment.utils.utils as utils
from django.http import JsonResponse, HttpResponse  # Json响应


'''
YWolfeee:
web_func.py中保留所有在views.py中使用到了和web发生交互但不直接在urls.py中暴露的函数
这些函数是views.py得以正常运行的工具函数。
函数比较乱，建议实现新函数时先在这里面找找有没有能用的。
'''

# 拉取用户头像使用的threads
long_request = requests.session()

# 获取用户头像的函数,返回路径&是否得到真正头像
def img_get_func(request):
    if request.session.get("img_path", None) is not None:
        # 有缓存
        return request.session['img_path'], False

    # 设置当头像无法加载时的位置
    default_img_name = 'pipi_square_iRGk72U.jpg'
    img_path = global_info.this_url + "/media/avatar/" + default_img_name

    # 尝试加载头像
    try:
        if global_info.account_auth:
            Sid = request.session['Sid']
            urls = global_info.img_url + "/getStuImg?stuId=" + Sid
            img_get = long_request.post(url=urls, verify=False, timeout=3)

            if img_get.status_code == 200:  # 接收到了学生信息
                img_path = eval(
                    img_get.content.decode('unicode-escape'))['path']
                img_path = global_info.login_url + img_path
                # 存入缓存
                request.session['img_path'] = img_path
                return img_path, True

    except:
        return img_path, False
        # 接受失败，返回旧地址
    return img_path, False


# added by pht
# 用于调整不同情况下判定标准的不同
def get_adjusted_qualified_rate(original_qualified_rate, appoint) -> float:
    '''
    get_adjusted_qualified_rate(original_qualified_rate : float, appoint) -> float:
        return an adjusted qualified rate according to appoint state
    '''
    min31 = timedelta(minutes=31)
    if appoint.Room.Rid == 'B214':                  # 暂时因无法识别躺姿导致的合格率下降
        original_qualified_rate -= 0.15             # 建议在0.1-0.2之间 前者最严 后者最宽松
    if appoint.Afinish - appoint.Astart < min31:    # 减少时间过短时前后未准时到的影响
        original_qualified_rate -= 0.05             # 建议在0-0.1间 暂未打算投入使用
    # if appoint.Areason == Appoint.Reason.R_LATE:    # 给未刷卡提供直接通过的机会
    #     original_qualified_rate += 0.25             # 建议在0.2-0.4之间 极端可考虑0.5 暂不使用
    return original_qualified_rate


def finishFunction(Aid):  # 结束预约时的定时程序
    # 变更预约状态
    appoint = Appoint.objects.get(Aid=Aid)
    mins15 = timedelta(minutes=15)
    # 避免直接使用全局变量! by pht
    adjusted_camera_qualified_check_rate = global_info.camera_qualified_check_rate
    try:
        # 如果处于进行中，表示没有迟到，只需检查人数
        if appoint.Astatus == Appoint.Status.PROCESSING:

            # 摄像头出现超时问题，直接通过
            if datetime.now() - appoint.Room.Rlatest_time > mins15:
                appoint.Astatus = Appoint.Status.CONFIRMED  # waiting
                appoint.save()
                utils.operation_writer(appoint.major_student.Sid, "顺利完成预约" +
                                 str(appoint.Aid) + ",设为Confirm", "func[finishAppoint]", "OK")
            else:
                if appoint.Acamera_check_num == 0:
                    utils.operation_writer(
                        global_info.system_log, "预约"+str(appoint.Aid)+"摄像头检测次数为0", "finishAppoint", "Problem")
                # 检查人数是否足够

                # added by pht: 需要根据状态调整 出于复用性和简洁性考虑在本函数前添加函数
                # added by pht: 同时出于安全考虑 在本函数中重定义了本地rate 稍有修改 避免出错
                adjusted_camera_qualified_check_rate = get_adjusted_qualified_rate(
                    original_qualified_rate=adjusted_camera_qualified_check_rate,
                    appoint=appoint,
                )

                if appoint.Acamera_ok_num < appoint.Acamera_check_num * adjusted_camera_qualified_check_rate - 0.01:  # 人数不足
                    status, tempmessage = utils.appoint_violate(
                        appoint, Appoint.Reason.R_TOOLITTLE)
                    if not status:
                        utils.operation_writer(global_info.system_log, "预约"+str(appoint.Aid) +
                                         "因人数不够而违约时出现异常: "+tempmessage, "func[finishAppoint]", "Error")

                else:   # 通过
                    appoint.Astatus = Appoint.Status.CONFIRMED
                    appoint.save()

        # 表示压根没刷卡
        elif appoint.Astatus == Appoint.Status.APPOINTED:
            # 特殊情况，不违约(地下室小舞台&康德，以及俄文楼)
            if (appoint.Room_id in {"B109A", "B207"}) or ('R' in appoint.Room_id):
                appoint.Astatus = Appoint.Status.CONFIRMED
                appoint.save()
                utils.operation_writer(appoint.major_student.Sid, "顺利完成预约" +
                                 str(appoint.Aid) + ",设为Confirm", "func[finishAppoint]", "OK")
            else:
                status, tempmessage = utils.appoint_violate(
                    appoint, Appoint.Reason.R_LATE)
                if not status:
                    utils.operation_writer(global_info.system_log, "预约"+str(appoint.Aid) +
                                     "因迟到而违约时出现异常: "+tempmessage, "func[finishAppoint]", "Error")

    # 如果上述过程出现不可预知的错误，记录
    except Exception as e:
        utils.operation_writer(global_info.system_log, "预约"+str(appoint.Aid)+"在完成时出现异常:" +
                         str(e)+",提交为waiting状态，请处理！", "func[finishAppoint]", "Error")
        appoint.Astatus = Appoint.Status.WAITING  # waiting
        appoint.save()


# 用于前端显示支持拼音搜索的人员列表
def get_student_chosen_list(request, get_all=False):
    js_stu_list = []
    Stu_all = Student.objects.all()
    for stu in Stu_all:
        if stu.Sid != request.session['Sid'] and (stu.superuser != 1 or get_all == True):
            js_stu_list.append({
                "id": stu.Sid,
                "text": stu.Sname + "_" + stu.Sid[:2],
                "pinyin": stu.pinyin
            })
    return js_stu_list


def get_talkroom_timerange(talk_room_list):
    """
    returns :talk toom 的时间range 以及最早和最晚的时间
    int, datetime.time, datetime.time
    """
    t_start = talk_room_list[0].Rstart
    t_finish = talk_room_list[0].Rfinish
    for room in talk_room_list:
        t_start = min(t_start, room.Rstart)
        t_finish = max(t_finish, room.Rfinish)
    return t_start, t_finish


def time2datetime(year, month, day, t):
    return datetime(year, month, day, t.hour, t.minute, t.second)

# modified by wxy
def getViolated_2(contents):
    try:
        student = Student.objects.get(Sid=contents['Sid'])
    except Exception as e:
        return JsonResponse(
            {'statusInfo': {
                'message': '学号不存在',
                'detail': str(e)
            }}, status=400)
    appoints = student.appoint_list.filter(Astatus=Appoint.Status.VIOLATED,
                                           major_student_id=student.Sid)
    data = [appoint.toJson() for appoint in appoints]
    return JsonResponse({'data': data}, status=200)


# added by wxy
def getStudent_2_classification(contents):
    #print('contents', contents)
    try:
        student = Student.objects.get(Sid=contents['Sid'])
    except Exception as e:
        return JsonResponse(
            {'statusInfo': {
                'message': '学号不存在',
                'detail': str(e)
            }}, status=400)

    present_day = datetime.now()
    seven_days_before = present_day - timedelta(7)
    appoints = []
    if contents['kind'] == 'future':
        appoints = student.appoint_list.filter(
            Astatus=Appoint.Status.APPOINTED).filter(Astart__gte=present_day)
    elif contents['kind'] == 'past':
        appoints = student.appoint_list.filter(
            (Q(Astart__lte=present_day) & Q(Astart__gte=seven_days_before))
            | (Q(Astart__gte=present_day)
               & ~Q(Astatus=Appoint.Status.APPOINTED)))
    elif contents['kind'] == 'today':
        appoints = student.appoint_list.filter(
            Astart__gte=present_day - timedelta(1),
            Astart__lte=present_day + timedelta(1))
    else:
        return JsonResponse(
            {
                'statusInfo': {
                    'message': '参数错误，kind取值应为past或future',
                    'detail': ''
                }
            },
            status=400)
    data = [appoint.toJson() for appoint in appoints]
    return JsonResponse({'data': data}, status=200)


# 对一个从Astart到Afinish的预约,考虑date这一天,返回被占用的时段
def timerange2idlist(Rid, Astart, Afinish, max_id):
    room = Room.objects.get(Rid=Rid)
    leftid = max(0, get_time_id(room, Astart.time()))
    rightid = min(get_time_id(room, Afinish.time(), 'leftopen'), max_id) + 1
    return range(leftid, rightid)

# 工具函数，用于前端展示预约



def get_hour_time(room, timeid):  # for room , consider its time id
    endtime_id = get_time_id(
        room, room.Rfinish, mode='leftopen')  # 返回最后一个时段的id
    if timeid > endtime_id + 1:  # 说明被恶意篡改，时间过大
        print("要求预约时间大于结束时间,返回23:59")
        return ("23:59"), False
    if (room.Rstart.hour + timeid // 2 == 24):
        return ("23:59"), True
    minute = room.Rstart.hour * 60 + room.Rstart.minute + timeid * 30
    opentime = time(minute // 60, minute % 60, 0)
    return opentime.strftime("%H:%M"), True



def get_time_id(room,
                ttime,
                mode="rightopen"):  # for room. consider a time's timeid
    if ttime < room.Rstart:  # 前置时间,返回-1必定可以
        return -1
    # 超过开始时间
    delta = timedelta(hours=ttime.hour - room.Rstart.hour,
                      minutes=ttime.minute - room.Rstart.minute)  # time gap
    hour = int(delta.total_seconds()) // 3600
    minute = int(delta.total_seconds() % 3600) // 60
    second = int(delta.total_seconds()) % 60
    #print("time_span:", hour, ":", minute,":",second)
    if mode == "rightopen":  # 左闭右开, 注意时间段[6:00,6:30) 是第一段
        half = 0 if minute < 30 else 1
    else:  # 左开右闭,(23:30,24:00]是最后一段
        half = 1 if (minute > 30 or (minute == 30 and second > 0)) else 0
        if minute == 0 and second == 0:  # 其实是上一个时段的末尾
            half = -1
    return hour * 2 + half


def get_dayrange(span=7):   # 获取用户的违约预约
    timerange_list = [{} for i in range(span)]
    present_day = datetime.now()
    for i in range(span):
        aday = present_day + timedelta(days=i)
        timerange_list[i]['weekday'] = aday.strftime("%a")
        timerange_list[i]['date'] = aday.strftime("%d %b")
        timerange_list[i]['year'] = aday.year
        timerange_list[i]['month'] = aday.month
        timerange_list[i]['day'] = aday.day
    return timerange_list

# added by wxy
def getStudentInfo(contents):   # 抓取学生信息的通用包
    try:
        student = Student.objects.get(Sid=contents['Sid'])
    except Exception as e:
        return JsonResponse(
            {'statusInfo': {
                'message': '学号不存在',
                'detail': str(e)
            }}, status=400)  # 好像需要再改一下...
    return {
        'Sname': student.Sname,
        'Sid': str(student.Sid),
        'Scredit': str(student.Scredit)
    }

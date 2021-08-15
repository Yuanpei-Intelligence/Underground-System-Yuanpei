# 数据库模型与操作
import os as os
import pypinyin  # 支持拼音搜索系统
from Appointment.models import Student, Room, Appoint, College_Announcement
from django.db.models import Q  # modified by wxy
from django.db import transaction  # 原子化更改数据库

# Http操作相关
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponse  # Json响应
from django.shortcuts import render, redirect  # 网页render & redirect
from django.urls import reverse
import json  # 读取Json请求

# csrf 检测和完善
from django.views.decorators.csrf import csrf_exempt
from django.middleware.csrf import get_token

# 时间任务
from datetime import datetime, timedelta, timezone, time, date
import random
import threading

# 全局参数读取
from YPUnderground import global_info, hash_identity_coder

# utils对接工具
from Appointment.utils.utils import send_wechat_message, appoint_violate, doortoroom, iptoroom, operation_writer, write_before_delete, cardcheckinfo_writer, check_temp_appoint
import Appointment.utils.web_func as web_func

# 定时任务注册
from django_apscheduler.jobstores import DjangoJobStore, register_events, register_job
from Appointment.utils.scheduler_func import scheduler
import Appointment.utils.scheduler_func as scheduler_func


# 验证时间戳
from time import mktime

# 注册启动以上schedule任务
register_events(scheduler)
scheduler.start()

'''

Views.py 使用说明
    尽可能把所有工具类的函数放到utils文件夹对应的py下，保持views.py中基本上是直接会被web调用的函数(见urls.py)
    如果存在一些依赖较多的函数，可以留在views.py中

'''

# 日志操作相关
# 返回数据的接口规范如下：(相当于不返回，调用函数写入日志)
# operation_writer(
#   user,
#   message,
#   source,
#   status_code
# )
# user表示这条数据对应的log记录对象，为Sid或者是system_log
# message记录这条log的主体信息
# source表示写入这个log的位置，表示为"func[函数名]"
# status_code为"OK","Problem","Error",其中Error表示需要紧急处理的问题，会发送到管理员的手机上

# 一些固定值
wklist = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def identity_check(request):    # 判断用户是否是本人
    # 是否需要检测

    if global_info.account_auth:

        try:
            return request.session['authenticated']
        except:
            pass

        try:
            # 认证通过
            d = datetime.utcnow()
            t = mktime(datetime.timetuple(d))
            assert float(t) - float(request.session['timeStamp']) < 3600.0
            assert hash_identity_coder.verify(request.session['Sid'] + request.session['timeStamp'],
                                              request.session['Secret']) is True
            request.session['authenticated'] = True
            return True

        except:
            return False
    else:
        return True

# 重定向到登录网站


def direct_to_login(request, islogout=False):
    params = request.build_absolute_uri('index')
    urls = global_info.login_url + "?origin=" + params
    #urls = 'http://localhost:8000/' + "?origin=" + params
    if islogout:
        urls = urls + "&is_logout=1"
    return urls


def obj2json(obj):
    return list(obj.values())


# def getToken(request):
#    return JsonResponse({'token': get_token(request)})


@csrf_exempt
def getAppoint(request):    # 班牌机对接程序
    if request.method == 'POST':  # 获取所有预约信息
        appoints = Appoint.objects.not_canceled()
        data = [appoint.toJson() for appoint in appoints]
        return JsonResponse({'data': data})
    elif request.method == 'GET':  # 获取某条预约信息
        try:
            Rid = request.GET.get('Rid', None)
            assert Rid is not None
            appoints = Appoint.objects.filter(Room_id=str(Rid))
        except Exception as e:
            return JsonResponse(
                {'statusInfo': {
                    'message': 'Room does not exist, please recheck Rid',
                    'detail': str(e)
                }},
                status=400)
        t1 = datetime.now()
        td = timedelta(days=1)
        t2 = t1 + td
        data = [appoint.toJson() for appoint in appoints if appoint.Astart >
                t1 and appoint.Astart < t2 and appoint.Astatus != Appoint.Status.CANCELED]
        try:
            assert len(data) > 0
        except:
            return JsonResponse(
                {
                    'data': data,
                    "empty": 1
                },
                status=200
            )
        return JsonResponse(
            {
                'data': data,
                "empty": 0
            }, status=200)


camera_lock = threading.RLock()


@csrf_exempt
def cameracheck(request):   # 摄像头post的后端函数

    # 获取摄像头信号，得到rid,最小人数
    try:
        ip = request.META.get("REMOTE_ADDR")
        temp_stu_num = int(
            eval(request.body.decode('unicode-escape'))['body']['people_num'])
        rid = iptoroom(ip.split(".")[3])  # !!!!!
        # rid = 'B221'  # just for debug
        room = Room.objects.get(Rid=rid)  # 获取摄像头信号
        num_need = room.Rmin  # 最小房间人数
    except:
        return JsonResponse({'statusInfo': {
            'message': '缺少摄像头信息!',
        }},
            status=400)
    now_time = datetime.now()

    # 存储上一次的检测时间
    room_previous_check_time = room.Rlatest_time

    # 更新现在的人数、最近更新时间
    try:
        with transaction.atomic():
            room.Rpresent = temp_stu_num
            room.Rlatest_time = now_time
            room.save()

    except Exception as e:
        operation_writer(global_info.system_log, "房间"+str(rid) +
                         "更新摄像头人数失败1: "+str(e), "func[cameracheck]", "Error")

        return JsonResponse({'statusInfo': {
            'message': '更新摄像头人数失败!',
        }},
            status=400)

    # 检查时间问题，可能修改预约状态；
    appointments = Appoint.objects.not_canceled().filter(
        Q(Astart__lte=now_time) & Q(Afinish__gte=now_time)
        & Q(Room_id=rid))  # 只选取状态在1，2之间的预约

    if len(appointments):  # 如果有，只能有一个预约
        content = appointments[0]
        if content.Atime.date() == content.Astart.date():
            # 如果预约时间在使用时间的24h之内 则人数下限为2
            num_need = min(global_info.today_min, num_need)
        try:
            if room.Rid in {"B109A", "B207"}:  # 康德报告厅&小舞台 不考虑违约
                content.Astatus = Appoint.Status.CONFIRMED
                content.save()
            else:  # 其他房间

                # added by wxy
                # 检查人数：采样、判断、更新
                # 人数在finishappoint中检查
                # modified by pht - 2021/8/15
                # 增加UNSAVED状态
                # 逻辑是尽量宽容，因为一分钟只记录两次，两次随机大概率只有一次成功
                # 所以没必要必须随机成功才能修改错误结果
                rand = random.uniform(0, 1)
                camera_lock.acquire()
                with transaction.atomic():
                    if now_time.minute != room_previous_check_time.minute or\
                        content.Acheck_status == Appoint.Check_status.UNSAVED: 
                        # 说明是新的一分钟或者本分钟还没有记录
                        # 如果随机成功，记录新的检查结果
                        if rand < global_info.check_rate:
                            content.Acheck_status = Appoint.Check_status.FAILED
                            content.Acamera_check_num += 1
                            if temp_stu_num >= num_need:  # 如果本次检测合规
                                content.Acamera_ok_num += 1
                                content.Acheck_status = Appoint.Check_status.PASSED
                        # 如果随机失败，锁定上一分钟的结果
                        elif content.Acheck_status == Appoint.Check_status.FAILED:
                            # 如果本次检测合规，宽容时也算上一次通过（因为一分钟只检测两次）
                            if temp_stu_num >= num_need:  
                                content.Acamera_ok_num += 1
                            # 本分钟暂无记录
                            content.Acheck_status = Appoint.Check_status.UNSAVED
                    else:
                        # 和上一次检测在同一分钟，此时：1.不增加检测次数 2.如果合规则增加ok次数
                        if content.Acheck_status == Appoint.Check_status.FAILED:
                            # 当前不合规；如果这次检测合规，那么认为本分钟合规
                            if temp_stu_num >= num_need:
                                content.Acamera_ok_num += 1
                                content.Acheck_status = Appoint.Check_status.PASSED
                        # else:当前已经合规，不需要额外操作
                    content.save()
                camera_lock.release()
                # add end
        except Exception as e:
            operation_writer(global_info.system_log, "预约"+str(content.Aid) +
                             "更新摄像头人数失败2: "+str(e), "func[cameracheck]", "Error")

            return JsonResponse({'statusInfo': {
                'message': '更新预约状态失败!',
            }},
                status=400)
        try:
            if now_time > content.Astart + timedelta(
                    minutes=15) and content.Astatus == Appoint.Status.APPOINTED:
                # added by wxy: 违约原因:迟到
                status, tempmessage = appoint_violate(
                    content, Appoint.Reason.R_LATE)
                if not status:
                    operation_writer(global_info.system_log, "预约"+str(content.Aid) +
                                     "因迟到而违约,返回值出现异常: "+tempmessage, "func[cameracheck]", "Error")
        except Exception as e:
            operation_writer(global_info.system_log, "预约"+str(content.Aid) +
                             "在迟到违约过程中: "+tempmessage, "func[cameracheck]", "Error")

        return JsonResponse({}, status=200)  # 返回空就好
    else:  # 否则的话 相当于没有预约 正常返回
        return JsonResponse({}, status=200)  # 返回空就好


@require_POST
@csrf_exempt
def cancelAppoint(request):
    # 身份确认检查
    if not identity_check(request):
        return redirect(direct_to_login(request))
    return scheduler_func.cancelFunction(request)


@csrf_exempt
def display_getappoint(request):    # 用于为班牌机提供展示预约的信息
    if request.method == "GET":
        try:
            Rid = request.GET.get('Rid')
            display_token = request.GET.get('token', None)
            check = Room.objects.filter(Rid=Rid)
            assert len(check) > 0

            assert display_token is not None
        except:
            return JsonResponse(
                {'statusInfo': {
                    'message': 'invalid params',
                }},
                status=400)
        if display_token != "display_from_underground":
            return JsonResponse(
                {'statusInfo': {
                    'message': 'invalid token:'+str(display_token),
                }},
                status=400)

        #appoint = Appoint.objects.get(Aid=3333)
        # return JsonResponse({'data': appoint.toJson()}, status=200,json_dumps_params={'ensure_ascii': False})
        nowdate = datetime.now().date()
        enddate = (datetime.now()+timedelta(days=3)).date()
        appoints = Appoint.objects.not_canceled().filter(
            Room_id=Rid
        ).order_by("Astart")

        data = [appoint.toJson() for appoint in appoints if
                appoint.Astart.date() >= nowdate and appoint.Astart.date() < enddate
                ]

        return JsonResponse({'data': data}, status=200, json_dumps_params={'ensure_ascii': False})
    else:
        return JsonResponse(
            {'statusInfo': {
                'message': 'method is not get',
            }},
            status=400)


# modified by wxy
# tag searchadmin_index
def admin_index(request):   # 我的账户也主函数
    # 用户校验
    if not identity_check(request):
        print(direct_to_login(request))
        return redirect(direct_to_login(request))
    warn_code = 0
    if request.GET.get("warn_code", None) is not None:
        warn_code = int(request.GET['warn_code'])
        warning = request.GET['warning']

    # 学生基本信息
    Sid = request.session['Sid']
    contents = {'Sid': str(Sid), 'kind': 'future'}
    my_info = web_func.getStudentInfo(contents)

    # 头像信息
    img_path, valid_path = web_func.img_get_func(request)
    if valid_path:
        request.session['img_path'] = img_path
    #img_path = global_info.this_url +  reverse("Appointment:web_func.img_get_func") + "?Sid=" + Sid

    # 分成两类,past future
    # 直接从数据库筛选两类预约
    appoint_list_future = json.loads(
        web_func.getStudent_2_classification(contents).content).get('data')
    contents['kind'] = 'past'
    appoint_list_past = json.loads(
        web_func.getStudent_2_classification(contents).content).get('data')

    # temptime.append(datetime.now())
    for x in appoint_list_future:
        x['Astart_hour_minute'] = datetime.strptime(
            x['Astart'], "%Y-%m-%dT%H:%M:%S").strftime("%I:%M %p")
        x['Afinish_hour_minute'] = datetime.strptime(
            x['Afinish'], "%Y-%m-%dT%H:%M:%S").strftime("%I:%M %p")
        appoint = Appoint.objects.get(Aid=x['Aid'])
        major_id = str(appoint.major_student_id)
        x['check_major'] = (Sid == major_id)

    # temptime.append(datetime.now())

    for x in appoint_list_past:
        x['Astart_hour_minute'] = datetime.strptime(
            x['Astart'], "%Y-%m-%dT%H:%M:%S").strftime("%I:%M %p")
        x['Afinish_hour_minute'] = datetime.strptime(
            x['Afinish'], "%Y-%m-%dT%H:%M:%S").strftime("%I:%M %p")
    appoint_list_future.sort(key=lambda k: k['Astart'])
    appoint_list_past.sort(key=lambda k: k['Astart'])
    appoint_list_past.reverse()

    return render(request, 'Appointment/admin-index.html', locals())


# modified by wxy
# tag searchadmin_credit
def admin_credit(request):
    if not identity_check(request):
        return redirect(direct_to_login(request))

    Sid = request.session['Sid']

    # 头像信息
    img_path, valid_path = web_func.img_get_func(request)
    if valid_path:
        request.session['img_path'] = img_path

    #img_path = global_info.this_url +  reverse("Appointment:web_func.img_get_func") + "?Sid=" + Sid

    contents = {'Sid': str(Sid)}
    vio_list = json.loads(web_func.getViolated_2(contents).content).get('data')
    vio_list_in_7_days = []
    present_day = datetime.now()
    seven_days_before = present_day - timedelta(7)
    for x in vio_list:
        temp_time = datetime.strptime(x['Astart'], "%Y-%m-%dT%H:%M:%S")
        x['Astart_hour_minute'] = temp_time.strftime("%I:%M %p")
        temp_time = datetime.strptime(x['Afinish'], "%Y-%m-%dT%H:%M:%S")
        x['Afinish_hour_minute'] = temp_time.strftime("%I:%M %p")
        if datetime.strptime(
                x['Astart'],
                "%Y-%m-%dT%H:%M:%S") <= present_day and datetime.strptime(
                    x['Astart'], "%Y-%m-%dT%H:%M:%S") >= seven_days_before:
            vio_list_in_7_days.append(x)
    vio_list_in_7_days.sort(key=lambda k: k['Astart'])
    my_info = web_func.getStudentInfo(contents)
    return render(request, 'Appointment/admin-credit.html', locals())


# added by wxy
@csrf_exempt
def door_check(request):  # 先以Sid Rid作为参数，看之后怎么改

    get_post = request.get_full_path().split("?")[1].split("&")
    get_post = {i.split("=")[0]: i.split("=")[1] for i in get_post}

    # 获取房间基本信息，如果是自习室就开门
    try:
        Sid, Rid = get_post['Sid'], get_post['Rid']

        assert Sid is not None
        assert Rid is not None

        student = Student.objects.get(Sid=Sid)

        Rid = doortoroom(Rid)
        all_room = Room.objects.all()
        all_rid = [room.Rid for room in all_room]
        if Rid[:4] in all_rid:  # 表示增加了一个未知的A\B号
            Rid = Rid[:4]
        room = None
        if Rid in all_rid:  # 如果在房间列表里，考虑类型
            room = Room.objects.get(Rid=Rid)
            if room.Rstatus == Room.Status.FORBIDDEN:  # 禁止使用的房间
                cardcheckinfo_writer(student, room, False, False)
                return JsonResponse(
                {
                    "code": 1,
                    "openDoor": "false",
                },
                status=400)
            if room.Rstatus == Room.Status.SUSPENDED:  # 自习室
                if room.RIsAllNight == Room.IsAllNight.Yes:  # 通宵自习室
                    cardcheckinfo_writer(student, room, True, True)
                    return JsonResponse({
                        "code": 0,
                        "openDoor": "true"
                    }, status=200)
                else: #不是通宵自习室
                    if datetime.now() >= datetime(datetime.now().year,datetime.now().month,datetime.now().day,room.Rstart.hour,room.Rstart.minute) and datetime.now() <= datetime(datetime.now().year,datetime.now().month,datetime.now().day,room.Rfinish.hour,room.Rfinish.minute):
                        # 在开放时间内
                        cardcheckinfo_writer(student, room, True, True)
                        return JsonResponse({
                        "code": 0,
                        "openDoor": "true"
                        },status=200)
                    else: #不在开放时间内
                        cardcheckinfo_writer(student, room, False, False)
                        return JsonResponse(
                        {
                            "code": 1,
                         "openDoor": "false",
                        },
                        status=400)
            # 否则是预约房，进入后续逻辑
        else:  # 不在房间列表
            raise SystemError
    except Exception as e:
        cardcheckinfo_writer(student, room, False, True)
        return JsonResponse(
            {
                "code": 1,
                "openDoor": "false",
            },
            status=400)

    # 检查预约者和房间是否匹配
    # contents = {'Sid': str(Sid), 'kind': 'today'}

    # --- modify by lhw: 临时预约 --- #
    now_time = datetime.now()
    appointments = Appoint.objects.not_canceled().filter(
        Q(Astart__lte=now_time) & Q(Afinish__gte=now_time)
        & Q(Room_id=Rid))  # 只选取当前时间位于预约时间段内的预约
    stu_appoint = student.appoint_list.not_canceled()
    # 获取刷卡者当前房间的可进行预约
    stu_appoint = [appoint for appoint in stu_appoint if appoint.Room_id == Rid
                   and appoint.Astart.date() == datetime.now().date()
                   and datetime.now() >= appoint.Astart-timedelta(minutes=15)
                   and datetime.now() <= appoint.Afinish+timedelta(minutes=15)]

    # 以下枚举所有无法开门情况
    if len(appointments) and len(stu_appoint) == 0:
        # 无法开门情况1：当前有预约，且自己没有15分钟内开始的预约。
        cardcheckinfo_writer(student, room, False, False)
        return JsonResponse(
            {
                "code": 1,
                "openDoor": "false",
            },
            status=400)

    if len(appointments) == 0 and len(stu_appoint) == 0:
        # 情况2：或许可以发起临时预约。
        # 首先检查该房间是否可以进行临时预约
        if check_temp_appoint(room) == False:
            cardcheckinfo_writer(student, room, False, False)
            return JsonResponse({
                "code": 1,
                "openDoor": "false"
            }, status=400)
        # 该房间可以用于临时预约，检查时间是否合法
        contents = {}
        contents['Rid'] = Rid
        contents['students'] = [Sid]
        contents['Sid'] = Sid
        contents['Astart'] = datetime(now_time.year, now_time.month, now_time.day,
                                      now_time.hour, now_time.minute, 0)  # 需要剥离秒级以下的数据，否则admin-index无法正确渲染
        timeid = web_func.get_time_id(room, time(contents['Astart'].hour, contents['Astart'].minute))
        endtime, valid = web_func.get_hour_time(room, timeid+1)

        # 注意，由于制度上不允许跨天预约，这里的逻辑也不支持跨日预约（比如从晚上23:00约到明天1:00）。
        contents['Afinish'] = datetime(now_time.year, now_time.month, now_time.day, int(
            endtime.split(':')[0]), int(endtime.split(':')[1]), 0)
        contents['non_yp_num'] = 0
        contents['Ausage'] = "临时预约"
        contents['announcement'] = ""
        contents['Atemp_flag'] = True
        # 合法条件：为避免冲突，临时预约时长必须超过15分钟；预约时在房间可用时段
        if (contents['Afinish'] - contents['Astart']) >= timedelta(minutes=15) and valid:
            response = scheduler_func.addAppoint(contents)
            if response.status_code == 200:
                stu_appoint = student.appoint_list.not_canceled()
                stu_appoint = [appoint for appoint in stu_appoint if appoint.Room_id == Rid
                               and appoint.Astart.date() == datetime.now().date()
                               and datetime.now() >= appoint.Astart-timedelta(minutes=15)
                               and datetime.now() <= appoint.Afinish+timedelta(minutes=15)]
                # 更新stu_appoint
            else:
                cardcheckinfo_writer(student, room, False, False)
                return JsonResponse(  # 无法预约（比如没信用分了）
                    {
                        "code": 1,
                        "openDoor": "false"
                    },
                    status=400)
        else:       # 预约时长不超过15分钟 或 预约时间不合法
            cardcheckinfo_writer(student, room, False, False)
            return JsonResponse({
                "code": 1,
                "openDoor": "false"
            }, status=400)

    # 以下情况都能开门
    ### --- modify end (2021.7.10) --- #
    '''
    # check the camera
    journal = open("journal.txt","a")
    journal.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    # journal.write('\t'+room.Rid+'\t')
    journal.write("开门\n")
    journal.close()
    '''
    try:
        with transaction.atomic():
            for now_appoint in stu_appoint:
                if (now_appoint.Astatus == Appoint.Status.APPOINTED and datetime.now() <=
                        now_appoint.Astart + timedelta(minutes=15)):
                    now_appoint.Astatus = Appoint.Status.PROCESSING
                    now_appoint.save()
    except Exception as e:
        operation_writer(global_info.system_log,
                         "可以开门却不开门的致命错误，房间号为" +
                         str(Rid) + ",学生为"+str(Sid)+",错误为:"+str(e),
                         "func[doorcheck]",
                         "Error")
        cardcheckinfo_writer(student, room, False, True)
        return JsonResponse(  # 未知错误
            {
                "code": 1,
                "openDoor": "false",
            },
            status=400)
    cardcheckinfo_writer(student, room, True, True)
    return JsonResponse({
        "code": 0,
        "openDoor": "true"
    }, status=200)

# tag searchindex


@csrf_exempt
def index(request):  # 主页
    search_code = 0
    warn_code = 0
    message_code = 0
    # 处理学院公告
    if (College_Announcement.objects.all()):
        try:
            message_item = College_Announcement.objects.get(
                show=College_Announcement.Show_Status.Yes)
            show_message = message_item.announcement
            message_code = 1
            # 必定只有一个才能继续
        except:
            message_code = 0
            # print("无法顺利呈现公告，原因可能是没有将状态设置为YES或者超过一条状态被设置为YES")

    # 用户校验
    if global_info.account_auth:
        # print("check", identity_check(request))
        if not identity_check(request):
            try:
                if request.method == "GET":
                    stu_id_ming = request.GET['Sid']
                    stu_id_code = request.GET['Secret']
                    timeStamp = request.GET['timeStamp']
                    request.session['Sid'] = stu_id_ming
                    request.session['Secret'] = stu_id_code
                    request.session['timeStamp'] = timeStamp
                    assert identity_check(request) is True

                else:  # POST 说明是display的修改,但是没登陆,自动错误
                    raise SystemError
            except:
                return redirect(direct_to_login(request))

                # 至此获得了登录的授权 但是这个人可能不存在 加判断
            try:
                request.session['Sname'] = Student.objects.get(
                    Sid=request.session['Sid']).Sname
            except:
                # 没有这个人 自动添加并提示
                if global_info.allow_newstu_appoint:
                    with transaction.atomic():
                        success = 1
                        try:
                            given_name = request.GET['name']
                        except:
                            given_name = "未命名"
                            success = 0
                        # 设置首字母
                        pinyin_list = pypinyin.pinyin(
                            given_name, style=pypinyin.NORMAL)
                        szm = ''.join([w[0][0] for w in pinyin_list])

                        student = Student(
                            Sid=request.session['Sid'],
                            Sname=given_name,
                            Scredit=3,
                            superuser=0,
                            pinyin=szm)

                        student.save()
                        request.session['Sname'] = given_name
                        warn_code = 1
                        if success == 1:
                            warn_message = "数据库不存在学生信息,已为您自动创建!"
                        else:
                            warn_message = "数据库不存在学生信息,已为您自动创建,请联系管理员修改您的姓名!"
                else:  # 学生不存在
                    request.session['Sid'] = "0000000000"
                    request.session['Secret'] = ""  # 清空信息
                    # request.session['Sname'] = Student.objects.get(
                    # Sid=request.session['Sid']).Sname
                    warn_code = 1
                    warn_message = "数据库不存在学生信息,请联系管理员添加!在此之前,您只能查看实时人数."

    else:
        request.session['Sid'] = global_info.debug_stuid
        request.session['Sname'] = Student.objects.get(
            Sid=request.session['Sid']).Sname

    # 处理信息展示
    room_list = Room.objects.all()
    display_room_list = room_list.filter(Rstatus=Room.Status.SUSPENDED).order_by('-Rtitle')
    talk_room_list = room_list.filter( # 研讨室
        Rtitle__icontains="研讨").filter(Rstatus=Room.Status.PERMITTED).order_by('Rmin', 'Rid')
    double_list = ['航模', '绘画', '书法']
    function_room_list = room_list.exclude( # 功能房
        Rid__icontains="R").filter(Rstatus=Room.Status.PERMITTED).exclude(Rtitle__icontains="研讨").union(
        room_list.filter(Q(Rtitle__icontains="绘画") | Q(
            Rtitle__icontains="航模") | Q(Rtitle__icontains="书法"))
    ).order_by('Rid')

    russian_room_list = room_list.filter(Rstatus=Room.Status.PERMITTED).filter( # 俄文楼
        Rid__icontains="R").order_by('Rid')
    russ_len = len(russian_room_list)
    if request.method == "POST":

        # YHT: added for Russian search
        request_time = request.POST.get("request_time", None)
        russ_request_time = request.POST.get("russ_request_time", None)
        check_type = ""
        if request_time is None and russ_request_time is not None:
            check_type = "russ"
            request_time = russ_request_time
        elif request_time is not None and russ_request_time is None:
            check_type = "talk"
        else:
            return render(request, 'Appointment/index.html', locals())

        if request_time != None and request_time != "":  # 初始加载或者不选时间直接搜索则直接返回index页面，否则进行如下反查时间
            day, month, year = int(request_time[:2]), int(
                request_time[3:5]), int(request_time[6:10])
            re_time = datetime(year, month, day)  # 获取目前request时间的datetime结构
            if re_time.date() < datetime.now().date():  # 如果搜过去时间
                search_code = 1
                search_message = "请不要搜索已经过去的时间!"
                return render(request, 'Appointment/index.html', locals())
            elif re_time.date() - datetime.now().date() > timedelta(days=6):
                # 查看了7天之后的
                search_code = 1
                search_message = "只能查看最近7天的情况!"
                return render(request, 'Appointment/index.html', locals())
            # 到这里 搜索没问题 进行跳转
            urls = reverse("Appointment:arrange_talk") + "?year=" + str(
                year) + "&month=" + str(month) + "&day=" + str(day) + "&type=" + check_type
            # YHT: added for Russian search
            return redirect(urls)

    return render(request, 'Appointment/index.html', locals())

# tag searcharrange_time


def arrange_time(request):
    if not identity_check(request):
        return redirect(direct_to_login(request))
    if request.method == 'GET':
        try:
            Rid = request.GET.get('Rid')
            print("Rid,", Rid, ",type,", type(Rid))
            check = Room.objects.filter(Rid=Rid)
            if not len(check):
                return redirect(reverse('Appointment:index'))
            room_object = check[0]

        except:
            # todo 加一个提示
            redirect(reverse('Appointment:index'))

    dayrange_list = web_func.get_dayrange()

    if room_object.Rstatus == Room.Status.FORBIDDEN:
        return render(request, 'Appointment/booking.html', locals())

    else:
        # 观察总共有多少个时间段
        time_range = web_func.get_time_id(
            room_object, room_object.Rfinish, mode="leftopen")
        for day in dayrange_list:  # 对每一天 读取相关的展示信息
            day['timesection'] = []
            temp_hour, temp_minute = room_object.Rstart.hour, int(
                room_object.Rstart.minute >= 30)

            for i in range(time_range + 1):  # 对每半个小时
                day['timesection'].append({})
                day['timesection'][-1]['starttime'] = str(
                   temp_hour + (i + temp_minute) // 2).zfill(2) + ":" + str(
                       (i + temp_minute) % 2 * 30).zfill(2)
                day['timesection'][-1]['status'] = 0  # 0可用 1已经预约 2已过
                day['timesection'][-1]['id'] = i
        # 筛选可能冲突的预约
        appoints = Appoint.objects.not_canceled().filter(
            Room_id=Rid,
            Afinish__gte=datetime(year=dayrange_list[0]['year'],
                                month=dayrange_list[0]['month'],
                                day=dayrange_list[0]['day'],
                                hour=0,
                                minute=0,
                                second=0),
            Astart__lte=datetime(year=dayrange_list[-1]['year'],
                                month=dayrange_list[-1]['month'],
                                day=dayrange_list[-1]['day'],
                                hour=23,
                                minute=59,
                                second=59))

        for appoint_record in appoints:
            change_id_list = web_func.timerange2idlist(Rid, appoint_record.Astart,
                                                   appoint_record.Afinish, time_range)
            for day in dayrange_list:
                if appoint_record.Astart.date() == date(day['year'], day['month'],
                                                    day['day']):
                    for i in change_id_list:
                        day['timesection'][i]['status'] = 1

        # 删去今天已经过去的时间
        present_time_id = web_func.get_time_id(room_object, datetime.now().time())
        for i in range(min(time_range, present_time_id) + 1):
            dayrange_list[0]['timesection'][i]['status'] = 1

        js_dayrange_list = json.dumps(dayrange_list)

        return render(request, 'Appointment/booking.html', locals())

# tag searcharrange_talk


def arrange_talk_room(request):

    if not identity_check(request):
        return redirect(direct_to_login(request))
    # search_time = request.POST.get('search_time')
    try:
        assert request.method == "GET"
        year = int(request.GET.get("year"))
        month = int(request.GET.get("month"))
        day = int(request.GET.get("day"))
        # YHT: added for russian search
        check_type = str(request.GET.get("type"))
        assert check_type in {"russ", "talk"}
        re_time = datetime(year, month, day)  # 如果有bug 直接跳转
        if re_time.date() < datetime.now().date() or re_time.date(
        ) - datetime.now().date() > timedelta(days=6):  # 这种就是乱改url
            return redirect(reverse("Appointment:idnex"))
        # 接下来判断时间
    except:
        return redirect(reverse("Appointment:index"))

    is_today = False
    if check_type == "talk":
        if re_time.date() == datetime.now().date():
            is_today = True
            show_min = global_info.today_min
        room_list = Room.objects.filter(
            Rtitle__contains='研讨').filter(Rstatus=Room.Status.PERMITTED).order_by('Rmin', 'Rid')
    else:  # type == "russ"
        room_list = Room.objects.filter(Rstatus=Room.Status.PERMITTED).filter(
            Rid__icontains="R").order_by('Rid')
    # YHT: added for russian search
    Rids = [room.Rid for room in room_list]
    t_start, t_finish = web_func.get_talkroom_timerange(
        room_list)  # 对所有讨论室都有一个统一的时间id标准
    t_start = web_func.time2datetime(year, month, day, t_start)  # 转换成datetime类
    t_finish = web_func.time2datetime(year, month, day, t_finish)
    t_range = int(((t_finish - timedelta(minutes=1)) -
                   t_start).total_seconds()) // 1800 + 1  # 加一是因为结束时间不是整点
    rooms_time_list = []  # [[{}] * t_range] * len(Rids)

    width = 100 / len(room_list)

    for sequence, room in enumerate(room_list):
        rooms_time_list.append([])
        for time_id in range(t_range):  # 对每半小时
            rooms_time_list[-1].append({})
            rooms_time_list[sequence][time_id]['status'] = 1  # 初始设置为1（不可预约）
            rooms_time_list[sequence][time_id]['time_id'] = time_id
            rooms_time_list[sequence][time_id]['Rid'] = Rids[sequence]
            temp_hour, temp_minute = t_start.hour, int(t_start.minute >= 30)
            rooms_time_list[sequence][time_id]['starttime'] = str(
                temp_hour + (time_id + temp_minute) // 2).zfill(2) + ":" + str(
                    (time_id + temp_minute) % 2 * 30).zfill(2)

    # 考虑三部分不可预约时间 1：不在房间的预约时间内 2：present_time之前的时间 3：冲突预约
    # 可能冲突的预约
    appoints = Appoint.objects.not_canceled().filter(Room_id__in=Rids,
                                                     Astart__gte=t_start,
                                                     Afinish__lte=t_finish)

    present_time_id = int(
        (datetime.now() - t_start).total_seconds()) // 1800  # 每半小时计 左闭右开

    for sequence, room in enumerate(room_list):
        # case 1
        start_id = int((web_func.time2datetime(year, month, day, room.Rstart) -
                        t_start).total_seconds()) // 1800
        finish_id = int(
            ((web_func.time2datetime(year, month, day, room.Rfinish) -
              timedelta(minutes=1)) - t_start).total_seconds()) // 1800
        #print(start_id,",", finish_id)
        for time_id in range(start_id, finish_id + 1):
            rooms_time_list[sequence][time_id]['status'] = 0
        print("in arrange talk room，present_time_id", present_time_id)
        # case 2
        for time_id in range(min(present_time_id + 1, t_range)):
            rooms_time_list[sequence][time_id]['status'] = 1

        # case 3
        for appointment in appoints:
            if appointment.Room.Rid == room.Rid:
                start_id = int(
                    (appointment.Astart - t_start).total_seconds()) // 1800
                finish_id = int(((appointment.Afinish - timedelta(minutes=1)) -
                                 t_start).total_seconds()) // 1800
                #print(start_id,",,,", finish_id)
                for time_id in range(start_id, finish_id + 1):
                    rooms_time_list[sequence][time_id]['status'] = 1

    js_rooms_time_list = json.dumps(rooms_time_list)
    js_weekday = json.dumps(
        {'weekday': wklist[datetime(year, month, day).weekday()]})

    return render(request, 'Appointment/booking-talk.html', locals())

# tag searchcheck_out


def check_out(request):  # 预约表单提交
    if not identity_check(request):
        return redirect(direct_to_login(request))
    temp_time = datetime.now()
    warn_code = 0
    try:
        if request.method == "GET":
            Rid = request.GET.get('Rid')
            weekday = request.GET.get('weekday')
            startid = request.GET.get('startid')
            endid = request.GET.get('endid')
        else:
            Rid = request.POST.get('Rid')
            weekday = request.POST.get('weekday')
            startid = request.POST.get('startid')
            endid = request.POST.get('endid')
        # 防止恶意篡改参数
        assert weekday in wklist
        assert int(startid) >= 0
        assert int(endid) >= 0
        assert int(endid) >= int(startid)
        appoint_params = {
            'Rid': Rid,
            'weekday': weekday,
            'startid': int(startid),
            'endid': int(endid)
        }
        room_object = Room.objects.filter(Rid=Rid)[0]
        dayrange_list = web_func.get_dayrange()
        for day in dayrange_list:
            if day['weekday'] == appoint_params['weekday']:  # get day
                appoint_params['date'] = day['date']
                appoint_params['starttime'], valid = web_func.get_hour_time(
                    room_object, appoint_params['startid'])
                assert valid is True
                appoint_params['endtime'], valid = web_func.get_hour_time(
                    room_object, appoint_params['endid'] + 1)
                assert valid is True
                appoint_params['year'] = day['year']
                appoint_params['month'] = day['month']
                appoint_params['day'] = day['day']
                # 最小人数下限控制
                appoint_params['Rmin'] = room_object.Rmin
                if datetime.now().strftime("%a") == appoint_params['weekday']:
                    appoint_params['Rmin'] = min(
                        global_info.today_min, room_object.Rmin)
        appoint_params['Sid'] = request.session['Sid']
        appoint_params['Sname'] = Student.objects.get(
            Sid=appoint_params['Sid']).Sname
        Stu_all = Student.objects.all()

    except:
        return redirect(reverse('Appointment:index'))
    if request.method == "GET":
        js_stu_list = web_func.get_student_chosen_list(request)
        return render(request, "Appointment/checkout.html", locals())
    elif request.method == 'POST':  # 提交预约信息
        contents = dict(request.POST)
        for key in contents.keys():
            if key != "students":
                contents[key] = contents[key][0]
                if key in {'year', 'month', 'day'}:
                    contents[key] = int(contents[key])
        # 处理外院人数
        if contents['non_yp_num'] == "":
            contents['non_yp_num'] = 0
        else:
            try:
                contents['non_yp_num'] = int(contents['non_yp_num'])
                assert contents['non_yp_num'] >= 0
            except:
                warn_code = 1
                warning = "外院人数有误,请按要求输入!"
                # return render(request, "Appointment/checkout.html", locals())
        # 处理用途未填写
        if contents['Ausage'] == "":
            warn_code = 1
            warning = "请输入房间用途!"
            # return render(request, "Appointment/checkout.html", locals())
        # 处理单人预约
        if "students" not in contents.keys():
            contents['students'] = [contents['Sid']]
        else:
            contents['students'].append(contents['Sid'])

        contents['Astart'] = datetime(contents['year'], contents['month'],
                                      contents['day'],
                                      int(contents['starttime'].split(":")[0]),
                                      int(contents['starttime'].split(":")[1]),
                                      0)
        contents['Afinish'] = datetime(contents['year'], contents['month'],
                                       contents['day'],
                                       int(contents['endtime'].split(":")[0]),
                                       int(contents['endtime'].split(":")[1]),
                                       0)
        if warn_code != 1:
            # 增加contents内容，这里添加的预约需要所有提醒，所以contents['new_require'] = 1
            contents['new_require'] = 1
            response = scheduler_func.addAppoint(
                contents)  # 否则没必要执行 并且有warn_code&message

            if response.status_code == 200:  # 成功预约
                urls = reverse(
                    "Appointment:admin_index"
                ) + "?warn_code=2&warning=预约" + room_object.Rtitle + "成功!"
                return redirect(urls)
            else:
                add_dict = eval(
                    response.content.decode('unicode-escape'))['statusInfo']
                warn_code = 1
                warning = add_dict['message']

        # 到这里说明预约失败 补充一些已有信息,避免重复填写
        js_stu_list = web_func.get_student_chosen_list(request)
        # selected_stu_list = Stu_all.filter(
        #    Sid__in=contents['students']).exclude(Sid=contents['Sid'])
        selected_stu_list = [
            w for w in js_stu_list if w['id'] in contents['students']]
        no_clause = True
        return render(request, 'Appointment/checkout.html', locals())


def logout(request):    # 登出系统
    if global_info.account_auth:
        request.session.flush()
        return redirect(direct_to_login(request, True))
        # return redirect(reverse("Appointment:index"))
    else:
        return redirect(reverse("Appointment:index"))

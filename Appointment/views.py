# 数据库模型与操作
from Appointment.utils.utils import send_wechat_message
import os as os
import pypinyin
from Appointment.utils.utils import operation_writer
from Appointment.utils.utils import write_before_delete
from Appointment.models import Student, Room, Appoint, College_Announcement
from django.db.models import Q  # modified by wxy
from django.db import transaction  # 原子化更改数据库

# Http操作相关
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponse  # Json响应
from django.shortcuts import render, redirect  # 网页render & redirect
from django.urls import reverse
import json  # 读取Json请求
import requests as requests

# csrf 检测和完善
from django.views.decorators.csrf import csrf_exempt
from django.middleware.csrf import get_token

# 时间和定时任务
from datetime import datetime, timedelta, timezone, time, date
from apscheduler.schedulers.background import BackgroundScheduler
from django_apscheduler.jobstores import DjangoJobStore, register_events, register_job
from Appointment.utils.utils import appoint_violate
import random
import threading

# 全局参数读取
from Appointment import global_info, hash_identity_coder

# 硬件对接工具
from Appointment.utils.utils import doortoroom, iptoroom


# 像微信发送消息

# 体验优化工具

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
# status_code为"OK","Problem","Error",其中Error表示需要紧急处理的问题

scheduler = BackgroundScheduler()
scheduler.add_jobstore(DjangoJobStore(), "default")




# 每周清除预约的程序，会写入logstore中
@register_job(scheduler, 'cron', id='ontime_delete', day_of_week='sat', hour='3', minute="30", second='0', replace_existing=True)
def clear_appointments():
    if global_info.delete_appoint_weekly:   # 是否清除一周之前的预约
        appoints_to_delete = Appoint.objects.filter(
            Afinish__lte=datetime.now()-timedelta(days=7))
        try:
            # with transaction.atomic(): //不采取原子操作
            write_before_delete(appoints_to_delete)  # 删除之前写在记录内
            appoints_to_delete.delete()
        except Exception as e:
            operation_writer(global_info.system_log, "定时删除任务出现错误: "+str(e),
                             "func[clear_appointments]", "Problem")

        # 写入日志
        operation_writer(global_info.system_log, "定时删除任务成功", "func[clear_appointments]")


# 注册启动以上schedule任务
register_events(scheduler)
scheduler.start()


# 是否开启登录系统，默认为开启
temp_stuid = ""
account_auth = True
# wechat_post_url 写在了utils.py

# tools
wklist = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
# 表示当天预约时放宽的人数下限
today_min = 2
# 是否允许不存在学生自动注册
allow_newstu_appoint = True
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
        if account_auth:
            Sid = request.session['Sid']
            #Sid = request.GET['Sid']
            urls = global_info.img_url + "/getStuImg?stuId=" + Sid
            img_get = long_request.post(url=urls, verify=False, timeout=3)

            if img_get.status_code == 200:  # 接收到了学生信息
                img_path = eval(
                    img_get.content.decode('unicode-escape'))['path']
                img_path = global_info.login_url + img_path
                # 存入缓存
                request.session['img_path'] = img_path

    except:
        return img_path, False
        # 接受失败，返回旧地址
    return img_path, True


def identity_check(request):    # 判断用户是否是本人
    # 是否需要检测
    if account_auth:

        try:
            # 认证通过
            assert hash_identity_coder.verify(request.session['Sid'],
                                              request.session['Secret']) is True
            return True

        except:
            return False
    else:
        return True

# 重定向到登录网站


def direct_to_login(request, islogout=False):
    params = request.build_absolute_uri('index')
    urls = global_info.login_url + "?origin=" + params
    if islogout:
        urls = urls + "&is_logout=1"
    return urls


def obj2json(obj):
    return list(obj.values())


def getToken(request):
    return JsonResponse({'token': get_token(request)})


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
            num_need = min(today_min, num_need)  # 如果预约时间在使用时间的24h之内 则人数下限为2
        try:
            if room.Rid in {"B109A", "B207"}:  # 康德报告厅&小舞台 不考虑违约
                content.Astatus = Appoint.Status.CONFIRMED
                content.save()
            else:  # 其他房间

                # added by wxy
                # 检查人数：采样、判断、更新
                # 人数在finishappoint中检查
                rand = random.uniform(0, 1)
                camera_lock.acquire()
                with transaction.atomic():
                    if rand > 1 - global_info.check_rate:
                        content.Acamera_check_num += 1
                        if temp_stu_num >= num_need:
                            content.Acamera_ok_num += 1
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


def finishAppoint(Aid):  # 结束预约时的定时程序
    # 变更预约状态
    appoint = Appoint.objects.get(Aid=Aid)
    mins15 = timedelta(minutes=15)
    adjusted_camera_qualified_check_rate = global_info.camera_qualified_check_rate  # 避免直接使用全局变量! by pht
    try:
        # 如果处于进行中，表示没有迟到，只需检查人数
        if appoint.Astatus == Appoint.Status.PROCESSING:

            # 摄像头出现超时问题，直接通过
            if datetime.now() - appoint.Room.Rlatest_time > mins15:
                appoint.Astatus = Appoint.Status.CONFIRMED  # waiting
                appoint.save()
                operation_writer(appoint.major_student.Sid, "顺利完成预约" +
                                 str(appoint.Aid) + ",设为Confirm", "func[finishAppoint]", "OK")
            else:
                if appoint.Acamera_check_num == 0:
                    operation_writer(
                        global_info.system_log, "预约"+str(appoint.Aid)+"摄像头检测次数为0", "finishAppoint", "Problem")
                # 检查人数是否足够

                # added by pht: 需要根据状态调整 出于复用性和简洁性考虑在本函数前添加函数
                # added by pht: 同时出于安全考虑 在本函数中重定义了本地rate 稍有修改 避免出错
                adjusted_camera_qualified_check_rate = get_adjusted_qualified_rate(
                    original_qualified_rate=adjusted_camera_qualified_check_rate,
                    appoint=appoint,
                )

                if appoint.Acamera_ok_num < appoint.Acamera_check_num * adjusted_camera_qualified_check_rate - 0.01:  # 人数不足
                    status, tempmessage = appoint_violate(
                        appoint, Appoint.Reason.R_TOOLITTLE)
                    if not status:
                        operation_writer(global_info.system_log, "预约"+str(appoint.Aid) +
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
                operation_writer(appoint.major_student.Sid, "顺利完成预约" +
                                 str(appoint.Aid) + ",设为Confirm", "func[finishAppoint]", "OK")
            else:
                status, tempmessage = appoint_violate(
                    appoint, Appoint.Reason.R_LATE)
                if not status:
                    operation_writer(global_info.system_log, "预约"+str(appoint.Aid) +
                                     "因迟到而违约时出现异常: "+tempmessage, "func[finishAppoint]", "Error")

    # 如果上述过程出现不可预知的错误，记录
    except Exception as e:
        operation_writer(global_info.system_log, "预约"+str(appoint.Aid)+"在完成时出现异常:" +
                         str(e)+",提交为waiting状态，请处理！", "func[finishAppoint]", "Error")
        appoint.Astatus = Appoint.Status.WAITING  # waiting
        appoint.save()


@csrf_exempt
def addAppoint(contents):  # 添加预约, main function

    # 首先检查房间是否存在
    try:
        room = Room.objects.get(Rid=contents['Rid'])
        assert room.Rstatus == Room.Status.PERMITTED, 'room service suspended!'
    except Exception as e:
        return JsonResponse(
            {
                'statusInfo': {
                    'message': '房间不存在或当前房间暂停预约服务,请更换房间!',
                    'detail': str(e)
                }
            },
            status=400)
    # 再检查学号对不对
    students_id = contents['students']  # 存下学号列表
    students = Student.objects.filter(
        Sid__in=students_id).distinct()  # 获取学生objects
    try:
        assert len(students) == len(
            students_id), "students repeat or don't exists"
    except Exception as e:
        return JsonResponse(
            {
                'statusInfo': {
                    'message': '预约人信息有误,请检查后重新发起预约!',
                    'detail': str(e)
                }
            },
            status=400)
    '''
    ideqstu = True
    noeq = ''
    try:
        for stu in contents['students']:
            student = Student.objects.get(Sid=stu['Sid'])
            if student.Sname != stu['Sname']:
                ideqstu = False
                noeq.join(str(student.Sid))
                noeq.join(' ')
            students.append(student)
    except Exception as e:
        return JsonResponse(
            {'statusInfo': {
                'message': '学号不存在',
                'detail': str(e)
            }}, status=400)
    # 再检查学号和人对不对
    if ideqstu is False:
        return JsonResponse(
            {'statusInfo': {
                'message': '学号姓名不匹配',
                'detail': noeq
            }}, status=400)
    '''
    # 检查人员信息
    try:
        #assert len(students) >= room.Rmin, f'at least {room.Rmin} students'
        real_min = room.Rmin if datetime.now().date(
        ) != contents['Astart'].date() else min(today_min, room.Rmin)
        assert len(students) + contents[
            'non_yp_num'] >= real_min, f'at least {room.Rmin} students'
    except Exception as e:
        return JsonResponse(
            {'statusInfo': {
                'message': '使用总人数需达到房间最小人数!',
                'detail': str(e)
            }},
            status=400)
    # 检查外院人数是否过多
    try:
        # assert len(
        #    students) >= contents['non_yp_num'], f"too much non-yp students!"
        assert 2 * len(
            students) >= real_min, f"too little yp students!"
    except Exception as e:
        return JsonResponse(
            {'statusInfo': {
                # 'message': '外院人数不得超过总人数的一半!',
                'message': '院内使用人数需要达到房间最小人数的一半!',
                'detail': str(e)
            }},
            status=400)

    # 检查如果是俄文楼，是否只有一个人使用
    if "R" in room.Rid:  # 如果是俄文楼系列
        try:
            assert len(
                students) + contents['non_yp_num'] == 1, f"too many people using russian room!"
        except Exception as e:
            return JsonResponse(
                {'statusInfo': {
                    'message': '俄文楼元创空间仅支持单人预约!',
                    'detail': str(e)
                }},
                status=400)

    # 检查预约时间是否正确
    try:
        #Astart = datetime.strptime(contents['Astart'], '%Y-%m-%d %H:%M:%S')
        #Afinish = datetime.strptime(contents['Afinish'], '%Y-%m-%d %H:%M:%S')
        Astart = contents['Astart']
        Afinish = contents['Afinish']
        assert Astart <= Afinish, 'Appoint time error'
        assert Astart > datetime.now(), 'Appoint time error'
    except Exception as e:
        return JsonResponse(
            {
                'statusInfo': {
                    'message': '非法预约时间段,请不要擅自修改url!',
                    'detail': str(e)
                }
            },
            status=400)
    # 预约是否超过3小时
    try:
        assert Afinish <= Astart + timedelta(hours=3)
    except:
        return JsonResponse({'statusInfo': {
            'message': '预约时常不能超过3小时!',
        }},
            status=400)
    # 学号对了，人对了，房间是真实存在的，那就开始预约了

    print('(⁎⁍̴̛ᴗ⁍̴̛⁎)')

    # 接下来开始搜索数据库，上锁
    try:
        with transaction.atomic():
            # 等待确认的和结束的肯定是当下时刻已经弄完的，所以不用管
            print("得到搜索列表")
            appoints = room.appoint_list.select_for_update().exclude(
                Astatus=Appoint.Status.CANCELED).filter(
                    Room_id=contents['Rid'])
            for appoint in appoints:
                start = appoint.Astart
                finish = appoint.Afinish

                # 第一种可能，开始在开始之前，只要结束的比开始晚就不行
                # 第二种可能，开始在开始之后，只要在结束之前就都不行
                if (start <= Astart < finish) or (Astart <= start < Afinish):
                    # 有预约冲突的嫌疑，但要检查一下是不是重复预约了
                    if start == Astart and finish == Afinish and appoint.Ausage == contents['Ausage'] \
                            and appoint.Aannouncement == contents['announcement'] and appoint.Ayp_num == len(students) \
                            and appoint.Anon_yp_num == contents['non_yp_num'] and contents['Sid'] == appoint.major_student_id:
                        # Room不用检查，肯定是同一个房间
                        operation_writer(
                            major_student.Sid, "重复发起同时段预约，预约号"+str(appoint.Aid), "func[addAppoint]", "OK")
                        return JsonResponse({'data': appoint.toJson()}, status=200)
                    else:
                        # 预约冲突
                        return JsonResponse(
                            {
                                'statusInfo': {
                                    'message': '预约时间与已有预约冲突,请重选时间段!',
                                    'detail': appoint.toJson()
                                }
                            },
                            status=400)
            # 获取预约发起者,确认预约状态
            try:
                major_student = Student.objects.get(Sid=contents['Sid'])
            except:
                return JsonResponse(
                    {
                        'statusInfo': {
                            'message': '发起人信息与登录信息不符,请不要在同一浏览器同时登录不同账号!',
                        }
                    },
                    status=400)

            # 确认信用分符合要求
            try:
                assert major_student.Scredit > 0
            except:
                return JsonResponse(
                    {'statusInfo': {
                        'message': '信用分不足,本月无法发起预约!',
                    }},
                    status=400)

            # 合法，可以返回了
            appoint = Appoint(Room=room,
                              Astart=Astart,
                              Afinish=Afinish,
                              Ausage=contents['Ausage'],
                              Aannouncement=contents['announcement'],
                              major_student=major_student,
                              Anon_yp_num=contents['non_yp_num'],
                              Ayp_num=len(students))
            appoint.save()
            for student in students:
                appoint.students.add(student)
            appoint.save()

            # write by cdf start2  # 添加定时任务：finish
            scheduler.add_job(finishAppoint,
                              args=[appoint.Aid],
                              id=f'{appoint.Aid}_finish',
                              next_run_time=Afinish)  # - timedelta(minutes=45))
            # write by cdf end2
            if datetime.now() <= appoint.Astart - timedelta(minutes=15):  # 距离预约开始还有15分钟以上，提醒有新预约&定时任务
                print('距离预约开始还有15分钟以上，提醒有新预约&定时任务',contents['new_require'])
                if contents['new_require'] == 1:  # 只有在非长线预约中才添加这个job
                    scheduler.add_job(send_wechat_message,
                                      args=[students_id,
                                            appoint.Astart,
                                            appoint.Room,
                                            "new",
                                            appoint.major_student.Sname,
                                            appoint.Ausage,
                                            appoint.Aannouncement,
                                            appoint.Anon_yp_num+appoint.Ayp_num,
                                            '',
                                            #appoint.major_student.Scredit,
                                            ],
                                      id=f'{appoint.Aid}_new_wechat',
                                      next_run_time=datetime.now() + timedelta(seconds=5))
                scheduler.add_job(send_wechat_message,
                                  args=[students_id,
                                        appoint.Astart,
                                        appoint.Room,
                                        "start",
                                        appoint.major_student.Sname,
                                        appoint.Ausage,
                                        appoint.Aannouncement,
                                        appoint.Ayp_num+appoint.Anon_yp_num,
                                        '',
                                        #appoint.major_student.Scredit,
                                        ],
                                  id=f'{appoint.Aid}_start_wechat',
                                  next_run_time=appoint.Astart - timedelta(minutes=15))
            else:  # 距离预约开始还有不到15分钟，提醒有新预约并且马上开始
                # send_status, err_message = send_wechat_message(students_id, appoint.Astart, appoint.Room,"new&start")
                scheduler.add_job(send_wechat_message,
                                  args=[students_id,
                                        appoint.Astart,
                                        appoint.Room,
                                        "new&start",
                                        appoint.major_student.Sname,
                                        appoint.Ausage,
                                        appoint.Aannouncement,
                                        appoint.Anon_yp_num+appoint.Ayp_num,
                                        '',
                                        #appoint.major_student.Scredit,
                                        ],
                                  id=f'{appoint.Aid}_new_wechat',
                                  next_run_time=datetime.now() + timedelta(seconds=5))

            operation_writer(major_student.Sid, "发起预约，预约号" +
                             str(appoint.Aid), "func[addAppoint]", "OK")

    except Exception as e:
        operation_writer(global_info.system_log, "学生" + str(major_student) +
                         "出现添加预约失败的问题:"+str(e), "func[addAppoint]", "Error")
        return JsonResponse({'statusInfo': {
            'message': '添加预约失败!请与管理员联系!',
        }},
            status=400)

    return JsonResponse({'data': appoint.toJson()}, status=200)


@require_POST
@csrf_exempt
def cancelAppoint(request):  # 取消预约
    if not identity_check(request):
        return redirect(direct_to_login(request))
    warn_code = 0
    try:
        Aid = request.POST.get('cancel_btn')
        appoints = Appoint.objects.filter(Astatus=Appoint.Status.APPOINTED)
        appoint = appoints.get(Aid=Aid)
    except:
        warn_code = 1
        warning = "预约不存在、已经开始或者已取消!"
        # return render(request, 'Appointment/admin-index.html', locals())
        return redirect(
            reverse("Appointment:admin_index") + "?warn_code=" +
            str(warn_code) + "&warning=" + warning)

    try:
        assert appoint.major_student.Sid == request.session['Sid']
    except:
        warn_code = 1
        warning = "请不要恶意尝试取消不是自己发起的预约！"
        # return render(request, 'Appointment/admin-index.html', locals())
        return redirect(
            reverse("Appointment:admin_index") + "?warn_code=" +
            str(warn_code) + "&warning=" + warning)

    if appoint.Astart < datetime.now() + timedelta(minutes=30):
        warn_code = 1
        warning = "不能取消开始时间在30分钟之内的预约!"
        return redirect(
            reverse("Appointment:admin_index") + "?warn_code=" +
            str(warn_code) + "&warning=" + warning)
    # 先准备发送人
    stu_list = [stu.Sid for stu in appoint.students.all()]
    with transaction.atomic():
        appoint_room_name = appoint.Room.Rtitle
        appoint.cancel()
        try:
            scheduler.remove_job(f'{appoint.Aid}_finish')
        except:
            operation_writer(global_info.system_log, "预约"+str(appoint.Aid) +
                             "取消时发现不存在计时器", 'func[cancelAppoint]', "Problem")
        operation_writer(appoint.major_student.Sid, "取消了预约" +
                         str(appoint.Aid), "func[cancelAppoint]", "OK")
        warn_code = 2
        warning = "成功取消对" + appoint_room_name + "的预约!"
    # send_status, err_message = send_wechat_message([appoint.major_student.Sid],appoint.Astart,appoint.Room,"cancel")
    # todo: to all
        print('will send cancel message')
        scheduler.add_job(send_wechat_message,
                          args=[stu_list,
                                appoint.Astart,
                                appoint.Room,
                                "cancel",
                                appoint.major_student.Sname,
                                appoint.Ausage,
                                appoint.Aannouncement,
                                appoint.Anon_yp_num+appoint.Ayp_num,
                                '',
                                #appoint.major_student.Scredit,
                                ],
                          id=f'{appoint.Aid}_cancel_wechat',
                          next_run_time=datetime.now() + timedelta(seconds=5))
    '''
    if send_status == 1:
        # 记录错误信息
        operation_writer(global_info.system_log, "预约" +
                             str(appoint.Aid) + "取消时向微信发消息失败，原因："+err_message, "func[addAppoint]", "Problem")
    '''

    # cancel wechat scheduler
    try:
        scheduler.remove_job(f'{appoint.Aid}_start_wechat')
    except:
        operation_writer(global_info.system_log, "预约"+str(appoint.Aid) +
                         "取消时发现不存在wechat计时器，但也可能本来就没有", 'func[cancelAppoint]', "Problem")

    return redirect(
        reverse("Appointment:admin_index") + "?warn_code=" + str(warn_code) +
        "&warning=" + warning)


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



@csrf_exempt
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

# 工具函数，用于前端展示预约


@csrf_exempt
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


@csrf_exempt
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


# 对一个从Astart到Afinish的预约,考虑date这一天,返回被占用的时段
@csrf_exempt
def timerange2idlist(Rid, Astart, Afinish, max_id):
    room = Room.objects.get(Rid=Rid)
    leftid = max(0, get_time_id(room, Astart.time()))
    rightid = min(get_time_id(room, Afinish.time(), 'leftopen'), max_id) + 1
    return range(leftid, rightid)


# modified by wxy
@csrf_exempt
def getStudent_2(contents):
    try:
        student = Student.objects.get(Sid=contents['Sid'])
    except Exception as e:
        return JsonResponse(
            {'statusInfo': {
                'message': '学号不存在',
                'detail': str(e)
            }}, status=400)
    appoints = student.appoint_list.all()
    data = [appoint.toJson() for appoint in appoints]
    return JsonResponse({'data': data}, status=200)


# added by wxy
@csrf_exempt
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
    my_info = getStudentInfo(contents)

    # 头像信息
    img_path, valid_path = img_get_func(request)
    if valid_path:
        request.session['img_path'] = img_path
    #img_path = global_info.this_url +  reverse("Appointment:img_get_func") + "?Sid=" + Sid

    # 分成两类,past future
    # 直接从数据库筛选两类预约
    appoint_list_future = json.loads(
        getStudent_2_classification(contents).content).get('data')
    contents['kind'] = 'past'
    appoint_list_past = json.loads(
        getStudent_2_classification(contents).content).get('data')

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


# modified by wxy
# tag searchadmin_credit
def admin_credit(request):
    if not identity_check(request):
        return redirect(direct_to_login(request))

    Sid = request.session['Sid']

    # 头像信息
    img_path, valid_path = img_get_func(request)
    if valid_path:
        request.session['img_path'] = img_path

    #img_path = global_info.this_url +  reverse("Appointment:img_get_func") + "?Sid=" + Sid

    contents = {'Sid': str(Sid)}
    vio_list = json.loads(getViolated_2(contents).content).get('data')
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
    my_info = getStudentInfo(contents)
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
        Rid = doortoroom(Rid)
        all_room = Room.objects.all()
        all_rid = [room.Rid for room in all_room]
        if Rid[:4] in all_rid:  # 表示增加了一个未知的A\B号
            Rid = Rid[:4]
        if Rid in all_rid:  # 如果在房间列表里，考虑类型
            if Room.objects.get(Rid=Rid).Rstatus == Room.Status.SUSPENDED:  # 自习室
                return JsonResponse({
                    "code": 0,
                    "openDoor": "true"
                }, status=200)
            # 否则是预约房，进入后续逻辑
        else:  # 不在房间列表
            raise SystemError

        student = Student.objects.get(Sid=Sid)
    except Exception as e:
        return JsonResponse(
            {
                "code": 1,
                "openDoor": "false",
            },
            status=400)

    # 检查预约者和房间是否匹配
    contents = {'Sid': str(Sid), 'kind': 'today'}
    stu_appoint = student.appoint_list.not_canceled()

    # 获取预约者今天的全部预约
    stu_appoint = [appoint for appoint in stu_appoint if appoint.Room_id == Rid
                   and appoint.Astart.date() == datetime.now().date()
                   and datetime.now() >= appoint.Astart-timedelta(minutes=15)
                   and datetime.now() <= appoint.Afinish+timedelta(minutes=15)]

    # 是这个房间and是今天的预约and在可开门时间范围内
    if len(stu_appoint) == 0:
        # 没有预约，或不在开门时间范围内
        return JsonResponse(
            {
                "code": 1,
                "openDoor": "false",
            },
            status=400)

    else:  # 到这里的一定是可以开门的
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
            return JsonResponse(  # 未知错误
                {
                    "code": 1,
                    "openDoor": "false",
                },
                status=400)
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
    if account_auth:
        if not identity_check(request):
            try:
                if request.method == "GET":
                    stu_id_ming = request.GET['Sid']
                    stu_id_code = request.GET['Secret']
                    request.session['Sid'] = stu_id_ming
                    request.session['Secret'] = stu_id_code
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
                if allow_newstu_appoint:
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
        request.session['Sid'] = temp_stuid
        request.session['Sname'] = Student.objects.get(
            Sid=request.session['Sid']).Sname

    # 处理信息展示
    room_list = Room.objects.all()
    display_room_list = room_list.filter(Rstatus=1).order_by('-Rtitle')
    talk_room_list = room_list.filter(
        Rstatus=0, Rtitle__icontains="研讨").order_by('Rmin', 'Rid')
    double_list = ['航模', '绘画', '书法']
    function_room_list = room_list.filter(
        Rstatus=0).exclude(Rid__icontains="R").exclude(Rtitle__icontains="研讨").union(
        room_list.filter(Q(Rtitle__icontains="绘画") | Q(
            Rtitle__icontains="航模") | Q(Rtitle__icontains="书法"))
    ).order_by('Rid')
    
    russian_room_list = room_list.filter(Rstatus=0).filter(
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

    dayrange_list = get_dayrange()
    # 观察总共有多少个时间段
    time_range = get_time_id(room_object, room_object.Rfinish, mode="leftopen")
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
        change_id_list = timerange2idlist(Rid, appoint_record.Astart,
                                          appoint_record.Afinish, time_range)
        for day in dayrange_list:
            if appoint_record.Astart.date() == date(day['year'], day['month'],
                                                    day['day']):
                for i in change_id_list:
                    day['timesection'][i]['status'] = 1

    # 删去今天已经过去的时间
    present_time_id = get_time_id(room_object, datetime.now().time())
    for i in range(min(time_range, present_time_id) + 1):
        dayrange_list[0]['timesection'][i]['status'] = 1

    js_dayrange_list = json.dumps(dayrange_list)

    return render(request, 'Appointment/booking.html', locals())


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
            show_min = today_min
        room_list = Room.objects.filter(
            Rtitle__contains='研讨', Rstatus=0).order_by('Rmin', 'Rid')
    else:  # type == "russ"
        room_list = Room.objects.filter(Rstatus=0).filter(
            Rid__icontains="R").order_by('Rid')
    # YHT: added for russian search
    Rids = [room.Rid for room in room_list]
    t_start, t_finish = get_talkroom_timerange(
        room_list)  # 对所有讨论室都有一个统一的时间id标准
    t_start = time2datetime(year, month, day, t_start)  # 转换成datetime类
    t_finish = time2datetime(year, month, day, t_finish)
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
        start_id = int((time2datetime(year, month, day, room.Rstart) -
                        t_start).total_seconds()) // 1800
        finish_id = int(
            ((time2datetime(year, month, day, room.Rfinish) -
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
        dayrange_list = get_dayrange()
        for day in dayrange_list:
            if day['weekday'] == appoint_params['weekday']:  # get day
                appoint_params['date'] = day['date']
                appoint_params['starttime'], valid = get_hour_time(
                    room_object, appoint_params['startid'])
                assert valid is True
                appoint_params['endtime'], valid = get_hour_time(
                    room_object, appoint_params['endid'] + 1)
                assert valid is True
                appoint_params['year'] = day['year']
                appoint_params['month'] = day['month']
                appoint_params['day'] = day['day']
                # 最小人数下限控制
                appoint_params['Rmin'] = room_object.Rmin
                if datetime.now().strftime("%a") == appoint_params['weekday']:
                    appoint_params['Rmin'] = min(today_min, room_object.Rmin)
        appoint_params['Sid'] = request.session['Sid']
        appoint_params['Sname'] = Student.objects.get(
            Sid=appoint_params['Sid']).Sname
        Stu_all = Student.objects.all()

    except:
        return redirect(reverse('Appointment:index'))
    if request.method == "GET":
        js_stu_list = get_student_chosen_list(request)
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
            response = addAppoint(contents)  # 否则没必要执行 并且有warn_code&message

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
        js_stu_list = get_student_chosen_list(request)
        # selected_stu_list = Stu_all.filter(
        #    Sid__in=contents['students']).exclude(Sid=contents['Sid'])
        selected_stu_list = [
            w for w in js_stu_list if w['id'] in contents['students']]
        no_clause = True
        return render(request, 'Appointment/checkout.html', locals())


def logout(request):    # 登出系统
    if account_auth:
        request.session.flush()
        return redirect(direct_to_login(request, True))
        # return redirect(reverse("Appointment:index"))
    else:
        return redirect(reverse("Appointment:index"))

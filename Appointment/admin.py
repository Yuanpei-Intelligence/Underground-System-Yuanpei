from Appointment.models import Student, Room, Appoint, College_Announcement
from django.contrib import admin, messages
from django.utils.html import format_html, format_html_join
from datetime import datetime, timedelta, timezone, time, date
from django.http import JsonResponse  # Json响应
from django.db import transaction  # 原子化更改数据库
from Appointment.views import addAppoint,scheduler
from Appointment.utils.utils import operation_writer,send_wechat_message
from Appointment import global_info
               

import pypinyin

# Register your models here.
admin.site.site_title = '元培地下室管理后台'
admin.site.site_header = '元培地下室 - 管理后台'

admin.site.register(College_Announcement)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    search_fields = ('Sid', 'Sname')
    list_display = ('Sid', 'Sname', 'Scredit', 'superuser')
    list_display_links = ('Sid', 'Sname')
    list_editable = ('Scredit', 'superuser')
    list_filter = ('Scredit', )
    fieldsets = (['基本信息', {
        'fields': (
            'Sid',
            'Sname',
        ),
    }], [
        '显示全部', {
            'classes': ('collapse', ),
            'description': '默认信息，不建议修改！',
            'fields': ('Scredit', 'superuser', 'pinyin'),
        }
    ])

    actions = ['recover', 'renew_pinyin']

    def recover(self, request, queryset):
        if not request.user.is_superuser:
            return self.message_user(request=request,
                                     message='操作失败,没有权限,请联系老师!',
                                     level=messages.WARNING)
        try:
            with transaction.atomic():
                stu_all = Student.objects.all()
                for stu in stu_all:
                    if stu.Scredit <= 2:
                        print(stu)
                        stu.Scredit += 1
                        stu.save()
                return self.message_user(request, '操作成功!')
            return self.message_user(request=request,
                                     message='操作失败!请与开发者联系!',
                                     level=messages.WARNING)
        except:
            return self.message_user(request=request,
                                     message='操作失败!请与开发者联系!',
                                     level=messages.WARNING)

    recover.short_description = "全院学生信用分恢复一分"

    def renew_pinyin(self, request, queryset):
        for stu in queryset:
            pinyin_list = pypinyin.pinyin(stu.Sname, style=pypinyin.NORMAL)
            stu.pinyin = ''.join([w[0][0] for w in pinyin_list])
            stu.save()
        return self.message_user(request=request,
                                 message='修改学生拼音成功!')

    renew_pinyin.short_description = "更新姓名拼音"


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('Rid', 'Rtitle', 'Rmin', 'Rmax', 'Rstart', 'Rfinish',
                    'Rstatus_display', 'Rpresent', 'Rlatest_time'
                    )  # 'is_delete'
    list_display_links = ('Rid', )
    list_editable = ('Rtitle', 'Rmin', 'Rmax', 'Rstart', 'Rfinish')
    search_fields = ('Rid', 'Rtitle')
    list_filter = ('Rstatus', )  # 'is_delete'
    fieldsets = (
        [
            '基本信息', {
                'fields': (
                    'Rid',
                    'Rtitle',
                    'Rmin',
                    'Rmax',
                    'Rstart',
                    'Rfinish',
                    'Rstatus',
                ),
            }
        ],
        # [
        #     '删除房间信息', {
        #         'classes': ('wide', ),
        #         'description': '逻辑删除不会清空物理内存。只是在这里进行标记',
        #         'fields': ('is_delete', ),
        #     }
        # ],
    )

    def Rstatus_display(self, obj):
        if obj.Rstatus == Room.Status.PERMITTED:
            color_code = 'green'
        elif obj.Rstatus == Room.Status.SUSPENDED:
            color_code = 'red'
        return format_html(
            '<span style="color: {};">{}</span>',
            color_code,
            obj.get_Rstatus_display(),
        )

    Rstatus_display.short_description = '预约状态'


@admin.register(Appoint)
class AppointAdmin(admin.ModelAdmin):
    search_fields = ('Aid', 'Room__Rtitle',
                     'major_student__Sname', 'Room__Rid', "students__Sname")
    list_display = (
        'Aid',
        'Room',
        'Astart',
        # 'Afinish',
        'Atime',  # 'Ausage',
        # 'major_student_display',
        'major_student_display',
        'Students',
        'total_display',
        'Astatus_display',
    )
    list_display_links = ('Aid', 'Room')
    list_editable = (
        'Astart',
        # 'Afinish',
    )  # 'Ausage'
    list_filter = ('Astart', 'Atime', 'Astatus')
    date_hierarchy = 'Astart'

    def Students(self, obj):
        return format_html_join('\n', '<li>{}</li>',
                                ((stu.Sname, ) for stu in obj.students.all()))

    Students.short_description = '参与人'

    def total_display(self, obj):
        return obj.Anon_yp_num + obj.Ayp_num

    total_display.short_description = "总人数"

    def major_student_display(self, obj):
        return obj.major_student.Sname

    major_student_display.short_description = "发起人"

    def Astatus_display(self, obj):
        status2color = {
            Appoint.Status.CANCELED: 'grey',
            Appoint.Status.APPOINTED: 'black',
            Appoint.Status.PROCESSING: 'purple',
            Appoint.Status.WAITING: 'blue',
            Appoint.Status.CONFIRMED: 'green',
            Appoint.Status.VIOLATED: 'red',
            Appoint.Status.JUDGED: 'yellowgreen',
        }
        color_code = status2color[obj.Astatus]
        return format_html(
            '<span style="color: {};">{}</span>',
            color_code,
            obj.get_status(),
        )

    Astatus_display.short_description = '预约状态'

    actions = ['confirm', 'violate', 'longterm1', 'longterm4', 'longterm8']

    def confirm(self, request, queryset):  # 确认通过
        if not request.user.is_superuser:
            return self.message_user(request=request,
                                     message='操作失败,没有权限,请联系老师!',
                                     level=messages.WARNING)
        some_invalid = 0
        have_success = 0
        try:
            with transaction.atomic():
                for appoint in queryset:
                    if appoint.Astatus == Appoint.Status.WAITING:
                        appoint.Astatus = Appoint.Status.CONFIRMED
                        appoint.save()
                        have_success = 1
                        # send wechat message
                        scheduler.add_job(send_wechat_message,
                                          args=[[appoint.major_student.Sid],  # stu_list
                                                appoint.Astart,  # start_time
                                                appoint.Room,     # room
                                                "confirm_admin_w2c",  # message_type
                                                appoint.major_student.Sname,  # major_student
                                                appoint.Ausage,  # usage
                                                appoint.Aannouncement,
                                                appoint.Ayp_num + appoint.Anon_yp_num,
                                                appoint.get_status(),  # reason
                                                # appoint.major_student.Scredit,
                                                ],
                                          id=f'{appoint.Aid}_confirm_admin_wechat',
                                          next_run_time=datetime.now() + timedelta(seconds=5))  # 5s足够了
                        operation_writer(global_info.system_log, str(appoint.Aid)+"号预约被管理员从WAITING改为CONFIRMED" +
                                 "发起人："+str(appoint.major_student), "func[admin:confirm]", "OK")
                    elif appoint.Astatus == Appoint.Status.VIOLATED:
                        appoint.Astatus = Appoint.Status.JUDGED
                        # for stu in appoint.students.all():
                        if appoint.major_student.Scredit < 3:
                            appoint.major_student.Scredit += 1
                            appoint.major_student.save()
                        appoint.save()
                        have_success = 1
                        # send wechat message
                        scheduler.add_job(send_wechat_message,
                                          args=[[appoint.major_student.Sid],  # stu_list
                                                appoint.Astart,  # start_time
                                                appoint.Room,     # room
                                                "confirm_admin_v2j",  # message_type
                                                appoint.major_student.Sname,  # major_student
                                                appoint.Ausage,  # usage
                                                appoint.Aannouncement,
                                                appoint.Ayp_num + appoint.Anon_yp_num,
                                                appoint.get_status(),  # reason
                                                #appoint.major_student.Scredit,
                                                ],
                                          id=f'{appoint.Aid}_confirm_admin_wechat',
                                          next_run_time=datetime.now() + timedelta(seconds=5))  # 5s足够了
                        operation_writer(global_info.system_log, str(appoint.Aid)+"号预约被管理员从VIOLATED改为JUDGED" +
                                 "发起人："+str(appoint.major_student), "func[admin:confirm]", "OK")

                    else:  # 不允许更改
                        some_invalid = 1

        except:
            return self.message_user(request=request,
                                     message='操作失败!请与开发者联系!',
                                     level=messages.WARNING)
        if not some_invalid:
            return self.message_user(request, "更改状态成功!")
        else:
            if have_success:
                return self.message_user(request=request,
                                         message='部分修改成功!但遭遇状态不为等待、违约的预约，这部分预约不允许更改!',
                                         level=messages.WARNING)
            else:
                return self.message_user(request=request,
                                         message='修改失败!不允许修改状态不为等待、违约的预约!',
                                         level=messages.WARNING)

    confirm.short_description = '所选条目 通过'

    def violate(self, request, queryset):  # 确认违约
        if not request.user.is_superuser:
            return self.message_user(request=request,
                                     message='操作失败,没有权限,请联系老师!',
                                     level=messages.WARNING)
        try:
            for appoint in queryset:
                assert appoint.Astatus != Appoint.Status.VIOLATED
                # if appoint.Astatus == Appoint.Status.WAITING:
                appoint.Astatus = Appoint.Status.VIOLATED
                appoint.Areason = Appoint.Reason.R_ELSE
                appoint.major_student.Scredit -= 1  # 只扣除发起人
                appoint.major_student.save()
                # for stu in appoint.students.all():
                #    stu.Scredit -= 1
                #    stu.save()
                appoint.save()

                # send wechat message
                scheduler.add_job(send_wechat_message,
                                  args=[[appoint.major_student.Sid],  # stu_list
                                        appoint.Astart,  # start_time
                                        appoint.Room,     # room
                                        "violate_admin",  # message_type
                                        appoint.major_student.Sname,  # major_student
                                        appoint.Ausage,  # usage
                                        appoint.Aannouncement,
                                        appoint.Ayp_num + appoint.Anon_yp_num,
                                        appoint.get_status(),  # reason
                                        #appoint.major_student.Scredit,
                                        ],
                                  id=f'{appoint.Aid}_violate_admin_wechat',
                                  next_run_time=datetime.now() + timedelta(seconds=5))  # 5s足够了
                operation_writer(global_info.system_log, str(appoint.Aid)+"号预约被管理员设为违约"+"发起人："+str(appoint.major_student), "func[admin:violate]", "OK")
        except:
            return self.message_user(request=request,
                                     message='操作失败!只允许对违约的条目操作!',
                                     level=messages.WARNING)

        
        return self.message_user(request, "设为违约成功!")

    violate.short_description = '所选条目 违约'

    def longterm_wk(self, request, queryset, week_num):
        if not request.user.is_superuser:
            return self.message_user(request=request,
                                     message='操作失败,没有权限,请联系老师!',
                                     level=messages.WARNING)
        if len(queryset) != 1:
            return self.message_user(request=request,
                                     message='每次仅允许将一条预约长线化!',
                                     level=messages.WARNING)
        for appoint in queryset:
            # print(appoint)
            try:
                with transaction.atomic():
                    stu_list = [stu.Sid for stu in appoint.students.all()]
                    for i in range(week_num):
                        # 调用函数完成预约
                        feedback = addAppoint({
                            'Rid':
                            appoint.Room.Rid,
                            'students':
                            stu_list,
                            'non_yp_num':
                            appoint.Anon_yp_num,
                            'Astart':
                            appoint.Astart + (i + 1) * timedelta(days=7),
                            'Afinish':
                            appoint.Afinish + (i + 1) * timedelta(days=7),
                            'Sid':
                            appoint.major_student.Sid,
                            'Ausage':
                            appoint.Ausage,
                            'announcement':
                            appoint.Aannouncement,
                            'new_require':  # 长线预约,不需要每一个都添加信息, 直接统一添加
                            0
                        })
                        if feedback.status_code != 200:  # 成功预约
                            warning = eval(
                                feedback.content.decode(
                                    'unicode-escape'))['statusInfo']['message']
                            print(warning)
                            raise Exception(warning)
                        '''
                        newappoint = Appoint(
                            Room=appoint.Room,
                            Astart=appoint.Astart + (i+1) * timedelta(days=7),
                            Afinish=appoint.Afinish +  (i+1) * timedelta(days=7),
                            Ausage=appoint.Ausage,
                            Aannouncement=appoint.Aannouncement,
                            major_student=appoint.major_student,
                            Anon_yp_num=appoint.Anon_yp_num,
                            Ayp_num=appoint.Ayp_num
                        )
                        newappoint.save()
                        for tempstudent in appoint.students.all():
                            print(tempstudent)
                            newappoint.students.add(tempstudent)
                        newappoint.save()
                        '''
            except Exception as e:
                operation_writer(global_info.system_log, "学生" + str(appoint.major_student) +
                                 "出现添加长线化预约失败的问题:"+str(e), "func[admin:longterm]", "Error")
                return self.message_user(request=request,
                                         message=str(e),
                                         level=messages.WARNING)

            # 到这里, 长线化预约发起成功
            scheduler.add_job(send_wechat_message,
                              args=[
                                  stu_list,  # stu_list
                                  appoint.Astart,  # start_time
                                  appoint.Room,     # room
                                  "longterm",  # message_type
                                  appoint.major_student.Sname,  # major_student
                                  appoint.Ausage,  # usage
                                  appoint.Aannouncement,
                                  len(stu_list) + appoint.Anon_yp_num,
                                  week_num,  # reason, 这里用作表示持续周数
                                  #appoint.major_student.Scredit,
                              ],
                              id=f'{appoint.Aid}_new_wechat',
                              next_run_time=datetime.now() + timedelta(seconds=5))  # 2s足够了
            operation_writer(appoint.major_student.Sid, "发起"+str(week_num) +
                             "周的长线化预约, 原始预约号"+str(appoint.Aid), "func[admin:longterm]", "OK")
        return self.message_user(request, '长线化成功!')

    def longterm1(self, request, queryset):
        week_num = 1  # 往后增加多少次
        return self.longterm_wk(request, queryset, week_num)

    def longterm4(self, request, queryset):
        week_num = 4  # 往后增加多少次
        return self.longterm_wk(request, queryset, week_num)

    def longterm8(self, request, queryset):
        week_num = 8  # 往后增加多少次
        return self.longterm_wk(request, queryset, week_num)

    longterm1.short_description = "增加一周本预约"
    longterm4.short_description = "增加四周本预约"
    longterm8.short_description = "增加八周本预约"

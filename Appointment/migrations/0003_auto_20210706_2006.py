# Generated by Django 3.1.1 on 2021-07-06 20:06

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Appointment', '0002_college_announcement'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='college_announcement',
            options={'verbose_name': '全院公告', 'verbose_name_plural': '全院公告'},
        ),
        migrations.AddField(
            model_name='appoint',
            name='Acamera_check_num',
            field=models.IntegerField(default=0, verbose_name='检查次数'),
        ),
        migrations.AddField(
            model_name='appoint',
            name='Acamera_ok_num',
            field=models.IntegerField(default=0, verbose_name='人数合格次数'),
        ),
        migrations.AddField(
            model_name='appoint',
            name='Areason',
            field=models.IntegerField(choices=[(0, 'R Noviolated'), (1, 'R Late'), (2, 'R Toolittle'), (3, 'R Else')], default=0, verbose_name='违约原因'),
        ),
        migrations.AddField(
            model_name='student',
            name='pinyin',
            field=models.CharField(max_length=20, null=True, verbose_name='拼音'),
        ),
        migrations.AlterField(
            model_name='appoint',
            name='Aannouncement',
            field=models.CharField(blank=True, max_length=256, null=True, verbose_name='预约通知'),
        ),
    ]
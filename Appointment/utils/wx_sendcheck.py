import urllib.request
import json
import platform
#--------------------------------
# 获取企业微信token
#--------------------------------

def get_token(url, corpid, corpsecret):
    token_url = '%s/cgi-bin/gettoken?corpid=%s&corpsecret=%s' % (url, corpid, corpsecret)
    token = json.loads(urllib.request.urlopen(token_url).read().decode())['access_token']
    return token

#--------------------------------
# 构建告警信息json
#--------------------------------
def messages(msg,wid):
    values = {
        "touser": wid,
        "msgtype": 'text',
        "agentid": '你的id', #偷懒没有使用变量了，注意修改为对应应用的agentid
        "text": {'content': msg},  #msg的数据类型str
        "safe": 0
        }
    msges=(bytes(json.dumps(values), 'utf-8'))
    return msges

#--------------------------------
# 发送告警信息
#--------------------------------
def send_message(url,token, data):
        send_url = '%s/cgi-bin/message/send?access_token=%s' % (url,token)
        respone=urllib.request.urlopen(urllib.request.Request(url=send_url, data=data)).read()
        x = json.loads(respone.decode())['errcode']
        # print(x)
        if x == 0:
            print ('Succesfully')
        else:
            print ('Failed')

##############函数结束########################

corpid = '你的appkey'
corpsecret = '你的secretkey'
url = 'https://qyapi.weixin.qq.com'
# msg='test,Python调用企业微信测试'

#函数调用
def wx_sendUser(msg,wid):
    test_token=get_token(url, corpid, corpsecret)
    msg_data= messages(msg,wid)
    send_message(url,test_token, msg_data)


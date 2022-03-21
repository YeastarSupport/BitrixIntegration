# !/usr/bin/python
# -*- coding:utf-8 -*-

from time import sleep
import requests
import hashlib
import json
import threading
import socket
import queue
import re
import os

#Automatic lead creation for new customers (whose numbers are not yet in CRM, excluding internal employee numbers)
#Raising the call card (lead/contact/company) at in/outbound call
#Raising a card when making a direct call from an IP phone (if you do not call by clicking on the phone number in CRM, but simply dial the number on the phone)
#Processing group call scenarios (if a call is made to a group of internal numbers, then the call card is raised for all users of this group, but after one of the users picks up the phone, the card is minimized for all the others)
#Processing call transfer scenarios (if a call is forwarded, the card of the call is minimized from the user from whom the call left and appears to the user to whom the call is forwarded)

list_PBX_callid = []
list_PBX_callernum = []
list_PBX_calleenum = []
list_Bitrix_callid = []
list_Bitrix_entityid = []
list_Bitrix_call_status = []

dict_Bitrix_userid = {}
dict_PBX_call_duration = {}

event_queue = queue.Queue()
clear_call_queue = queue.Queue()

BUFSIZE = 1024

global_ac_token = ''
encoding = 'utf-8'
is_exit = 'false'

B_CALL_STATUS_RING = 1
B_CALL_STATUS_ANSWERED = 2
B_CALL_STATUS_END = 3
list_callstatus = ['none', 'ring', 'answered', 'end']

pbx_url = ''
basic_url = ''

bitrix_basic_url = ''
api_username = ''
api_password = ''

#--------------------------------------------------------------------------
#                        PBX API
#--------------------------------------------------------------------------
def post_request(url,data,print_r):
    if data == '':
        r = requests.post(url,verify=False)
    else:
        r = requests.post(url,data,verify=False)

    if print_r == 1:
        print(r)

    return r

def login_api(input_username, input_password):
    method_login = 'login'

    username = input_username

    hash_md5 = hashlib.md5()
    hash_md5.update(input_password.encode('utf-8'))
    password = hash_md5.hexdigest()

    #print(username)
    #print(password)

    login_url = basic_url+method_login
    login_data = '{"username": \"%s\","password": \"%s\","port": "8260", "version": "2.0.0"}' %(username,password)

    print('login to API....')
    #print(login_url)
    #print(login_data)
    r = post_request(login_url, login_data, 1)
    #r = requests.post(login_url,data=login_data,verify=False)
    print(r.text)
    return r.text

def api_login(username, password):
    global global_ac_token
    
    print('login api')

    login_resp_text=login_api(username, password)
    login_resp_json = json.loads(login_resp_text)
    #print('login status: '+login_resp_json['status'])
    
    global_ac_token = login_resp_json['token']
    print('access token: ',global_ac_token)

def send_heartbeat(global_ac_token, ipaddr):
    method = 'heartbeat'
    request_url = basic_url+method+'?token='+global_ac_token
    heartbeat_data = '{"ipaddr":\"%s\", "port":8260,"version":"2.0.0"}' %ipaddr
    print(request_url)
    print(heartbeat_data)
    print('send heartbeat')

    post_request(request_url,heartbeat_data,1)


def start_keepalive_timer():

    print('start keepalive timer')
    send_heartbeat(global_ac_token,'192.168.29.12')

    t = threading.Timer(30, start_keepalive_timer)
    t.start()


def query_extensionlist():
    #/api/v2.0.0/extension/list?token=7dff09fe45414a4f340e978e274b53ea
    method = 'extension/list'
    request_url = basic_url+method+'?token='+global_ac_token
    print(request_url)
    print('query extension list')

    r = post_request(request_url,'',1)
    print(r.text)

def query_calldetails(callid):

    method = 'call/query'
    request_url = basic_url+method+'?token='+global_ac_token
    print(request_url)

    data = '{"callid": %s}' %callid

    r = post_request(request_url,data,1)
    print(r.text)
    return r.text

#--------------------------------------------------------------------------
#                        Bitrix相关 API
#--------------------------------------------------------------------------
def bitrix_getcalltype(typeofcall):
#Type of call:
#1 - outbound
#2 - inbound
#3 - inbound with forwarding
#4 - callback

    if typeofcall == 'outbound':
        calltype = 'TYPE=1'
    elif typeofcall == 'inbound':
        calltype = 'TYPE=2'
    elif typeofcall == 'inbound_forwarding':
        calltype = 'TYPE=3'
    elif typeofcall == 'callback':
        calltype = 'TYPE=4'

    return calltype


def bitrix_get_userid(callee_num):
    
    if callee_num in dict_Bitrix_userid:
        return 'USER_ID=' + dict_Bitrix_userid[callee_num]

def bitrix_telephonycallregister(call_number,typeofcall,userid):

    #https://b24-0y2sg2.bitrix24.com/rest/1/hik5ei26fwlbffi5/telephony.externalcall.register.json?USER_ID=1&TYPE=2&PHONE_NUMBER=2012348886
    print('|Bitrix call| register',call_number)
    
    calltype = bitrix_getcalltype(typeofcall)

    method = 'telephony.externalcall.register.json'
    phone_num = 'PHONE_NUMBER='+call_number

    request_url = bitrix_basic_url + method + '?' + userid +'&' + calltype + '&' + phone_num + '&' + 'CRM_CREATE=1'
    r = requests.get(request_url)
    register_call_json = json.loads(r.text)
    
    print(request_url)
    print(register_call_json)
    return register_call_json


def bitrix_telephonycallshow(callee_num,callid,userid):
    
    print('|Bitrix call| show',callee_num)

    method = 'telephony.externalcall.show.json'

    index = get_callindex(callee_num,callid)
    if index != -1:
        bitrix_callid = 'CALL_ID=' + list_Bitrix_callid[index]

    request_url = bitrix_basic_url + method + '?' + userid + '&' + bitrix_callid
    r = requests.get(request_url)
    callshow_json = json.loads(r.text)
    print(request_url)
    print(callshow_json)


def bitrix_telephonycallfinish(callee_num,callid,userid,duration,reason):
    
    failed_reason = ''
    print('|Bitrix call| finish',callee_num)
    #bitrix_callid = list_Bitrix_callid[get_callindex(callee_num,callid)]


    index = get_callindex(callee_num,callid)
    if index != -1:
        bitrix_callid = '&' + 'CALL_ID=' + list_Bitrix_callid[index]

    call_duration = '&' + 'DURATION=' + duration
    
    if reason != 'ANSWERED' and reason != '':
        failed_reason = '&' + 'FAILED_REASON=' + reason

    method = 'telephony.externalcall.finish.json'
    request_url = bitrix_basic_url + method + '?' + userid + bitrix_callid + call_duration + failed_reason
    r = requests.get(request_url)
    callfinish_json = json.loads(r.text)
    print(request_url)
    print(callfinish_json)

def bitrix_telephonycallhide(callee_num,callid):

    print('|Bibrix call| hide', callee_num)
    userid = bitrix_get_userid(callee_num)
    index = get_callindex(callee_num,callid)
    if index != -1:
        bitrix_callid = '&' + 'CALL_ID=' + list_Bitrix_callid[index]

    method = 'telephony.externalcall.hide'
    request_url = bitrix_basic_url + method + '?' + userid + bitrix_callid
    r = requests.get(request_url)
    callhide_json = json.loads(r.text)
    print(request_url)
    print(callhide_json)


def bitrix_userget():

    #https://b24-u42rxz.bitrix24.com/rest/1/qqixw3op23noglsx/user.get.json
    method = 'user.get'
    request_url = bitrix_basic_url+method
    r = requests.get(request_url)
    users_info = json.loads(r.text)
    return users_info


#--------------------------------------------------------------------------
#                        通话处理 API
#--------------------------------------------------------------------------

def append_data_tolist(callid,caller_num,callee_num,bitrix_callid,bitrix_callstatus):

    print('|call status| append call information to the list ',callee_num)
    list_PBX_callid.append(callid)
    list_PBX_callernum.append(caller_num)
    list_PBX_calleenum.append(callee_num)
    list_Bitrix_callid.append(bitrix_callid)
    list_Bitrix_call_status.append(bitrix_callstatus)


#根据被叫号码查找index
def get_callindex(callee_num,callid):
    n = 0
    
    for num in list_PBX_calleenum:
        if num == callee_num: #先匹配被叫号码
            if callid == list_PBX_callid[n]:#再匹配callid
                return n 
        n += 1
    return -1

def get_callindex_by_callernum(caller_num,callid):
    n = 0
    
    for num in list_PBX_callernum:
        if num == caller_num: #先匹配被叫号码
            if callid == list_PBX_callid[n]:#再匹配callid
                return n 
        n += 1
    return -1

def get_callduration(callee_num,callid):
    print('get call duration')


def print_calllist(index):
    if index == -1:
        print('cannot find the call index')
        return
    
    print('|call print| index: ',index)
    print('|call print| callid: ', list_PBX_callid[index])
    print('|call print| caller number: ',list_PBX_callernum[index])
    print('|call print| callee number: ', list_PBX_calleenum[index])
    print('|call print| bitrix callid: ', list_Bitrix_callid[index])

def del_calllist(index):
    print('|call clear| delete index: ',index)
    del list_PBX_callid[index]
    del list_PBX_callernum[index]
    del list_PBX_calleenum[index]
    del list_Bitrix_call_status[index]
    del list_Bitrix_callid[index]

#通话响铃
def inbound_call_ring(callid,caller_num,callee_num):
    bitrix_callid = ''
    userid = ''

    #如果被叫号码和callid都匹配才算是同一路通话
    index = get_callindex(callee_num,callid)
    if index > -1:
        print('|inbound call check| call exist and status is ', list_callstatus[list_Bitrix_call_status[index]])
        return

    print('|inbound call status| ringing, callee number: '+ callee_num +' callid: ' + callid)

    #根据被叫号码获取Bitrix对应号码的userid用于弹屏
    userid = bitrix_get_userid(callee_num)

    #调用bitrix popup
    register_call_json = bitrix_telephonycallregister(caller_num,'inbound',userid)
    bitrix_callid = register_call_json['result']['CALL_ID']
    #CRM_ENTITY_ID = register_call_json['result']['CRM_ENTITY_ID']

    #把通话数据存入list，根据index绑定PBX和bitrix通话信息
    append_data_tolist(callid,caller_num,callee_num,bitrix_callid, B_CALL_STATUS_RING)

#通话建立
def inbound_call_answered(callee_num,callid):

    index = get_callindex(callee_num,callid)
    if index == -1:
        print('|call check| call not found, return')
        return
    elif list_Bitrix_call_status[index] >= B_CALL_STATUS_ANSWERED:
        print('|call check| call already answered')
        return

    print('|call status| answered, callee number: ', list_PBX_calleenum[index])
    print_calllist(index)
    list_Bitrix_call_status[index] = B_CALL_STATUS_ANSWERED

    userid = bitrix_get_userid(callee_num)

    #更新bitrix通话状态
    bitrix_telephonycallshow(callee_num,callid,userid)

    #清理其他同callid的分机状态
    clear_other_calls(list_PBX_callid[index], callee_num)
#通话结束

def inbound_call_end(callee_num,callid, need_report, call_duration,reason):

    index = get_callindex(callee_num,callid)
    if index == -1:
        print('|call check| call maybe deleted already ')
        return -1
    elif list_Bitrix_call_status[index] == B_CALL_STATUS_END:
        print('|call check| call already ended ')
        if need_report:
            userid = bitrix_get_userid(callee_num)
            #更新bitrix通话状态 通话建立后的上报需要等到cdr事件以后才能获取到通话时间和失败原因
            bitrix_telephonycallfinish(callee_num,callid,userid,call_duration,reason)
        return 0
    
    print('|call status| end, callee number: ', list_PBX_calleenum[index])
    
    if need_report:
        userid = bitrix_get_userid(callee_num)
        #更新bitrix通话状态 通话建立后的上报需要等到cdr事件以后才能获取到通话时间和失败原因
        bitrix_telephonycallfinish(callee_num,callid,userid,call_duration,reason)

    list_Bitrix_call_status[index] = B_CALL_STATUS_END

def outbound_call_alert(callid,caller_num,callee_num):
    bitrix_callid = ''
    userid = ''

    #如果被叫号码和callid都匹配才算是同一路通话
    index = get_callindex_by_callernum(caller_num,callid)
    if index > -1:
        print('|outbound call check| call exist and status is ', list_callstatus[list_Bitrix_call_status[index]])
        return

    print('|outbound call status| ringing, caller number: '+ caller_num +' callid: ' + callid)

    #根据主叫号码获取Bitrix对应号码的userid用于弹屏
    userid = bitrix_get_userid(caller_num)

    #调用bitrix popup
    register_call_json = bitrix_telephonycallregister(callee_num,'outbound',userid)
    bitrix_callid = register_call_json['result']['CALL_ID']
    #CRM_ENTITY_ID = register_call_json['result']['CRM_ENTITY_ID']

    #把通话数据存入list，根据index绑定PBX和bitrix通话信息
    append_data_tolist(callid,caller_num,callee_num,bitrix_callid, B_CALL_STATUS_RING)

def outbound_call_answered(caller_num,callee_num,callid):
    index = get_callindex_by_callernum(caller_num,callid)
    if index == -1:
        print('|outbound call check| call not found, return')
        return
    elif list_Bitrix_call_status[index] >= B_CALL_STATUS_ANSWERED:
        print('|outbound call check| call already answered')
        return

    print('|outbound call status| answered, caller number: ', list_PBX_callernum[index])
    print_calllist(index)
    list_Bitrix_call_status[index] = B_CALL_STATUS_ANSWERED

    userid = bitrix_get_userid(caller_num)

    #更新bitrix通话状态
    bitrix_telephonycallshow(callee_num,callid,userid)

def outbound_call_end(callee_num,caller_num,callid, need_report, call_duration,reason):

    index = get_callindex_by_callernum(caller_num,callid)
    if index == -1:
        print('|call check| call maybe deleted already ')
        return -1
    elif list_Bitrix_call_status[index] == B_CALL_STATUS_END:
        print('|call check| call already ended ')
        if need_report:
            userid = bitrix_get_userid(caller_num)
            #更新bitrix通话状态 通话建立后的上报需要等到cdr事件以后才能获取到通话时间和失败原因
            bitrix_telephonycallfinish(callee_num,callid,userid,call_duration,reason)
        return 0
    
    print('|call status| end, caller number: ', list_PBX_callernum[index])
    
    if need_report:
        userid = bitrix_get_userid(caller_num)
        #更新bitrix通话状态 通话建立后的上报需要等到cdr事件以后才能获取到通话时间和失败原因
        bitrix_telephonycallfinish(callee_num,callid,userid,call_duration,reason)

    list_Bitrix_call_status[index] = B_CALL_STATUS_END

def inbound_call_hide(callee_num,callid):

    bitrix_telephonycallhide(callee_num,callid)

#响铃组场景，一个分机接起 要结束其他分机的通话
def clear_other_calls(callid,callee_num):

    index = 0
    check_callid = ''

    print('|call status| end other calls for callid: ',callid)

    #遍历找出同callid的其他分机
    for check_callid in list_PBX_callid:
        if callid == check_callid:
            if callee_num != list_PBX_calleenum[index]:
                inbound_call_hide(list_PBX_calleenum[index],callid)
        index += 1

def report_inbound_cancel_calls(callid):
        
    if list_PBX_callid.count(callid) == 0:
        print('|Bitrix call| no need to report cancel calls')
        return

    print('|Bitrix call| report cancel calls')

    n = 0
    #根据callid找到通话和被叫号码进行上报
    for call_id in list_PBX_callid:
        if call_id == callid: 
            bitrix_telephonycallfinish(list_PBX_calleenum[n],callid,'0','NO ANSWER')
        n += 1
    return -1
    #通话没有建立

def delete_calls_bycallid(callid):
    print('|call clear| callid: ',callid)
    index = 0
    
    for index in range(len(list_PBX_callid)-1,-1,-1):
        if list_PBX_callid[index] == callid:
            del_calllist(index)


def check_callee_entity(callee_num):

    if callee_num in dict_Bitrix_userid:
        return True
    else:
        return False

#inbound来电处理
def inbound_call_handler(callid,inbound_data,ext_data):
    #print(inbound_data)
    #print(ext_data)
    inbound_caller_num = ''
    inbound_callee_num = ''
    inbound_member_status = ''
    ext_member_status = ''
    ext_number = ''

    if inbound_data != '':
        inbound_member_status = inbound_data['memberstatus']
        inbound_caller_num = inbound_data['from']
        inbound_callee_num = inbound_data['to']

    if ext_data != '':
        ext_member_status = ext_data['memberstatus']
        ext_number = ext_data['number']

    #检查被叫号码是否是CRM绑定的号码，不是则不用上报
    if check_callee_entity(ext_number) == False:
        print('|Bitrix user| not a bitrix user', ext_number)
        return

    #通话结束状态 任意一方"memberstatus":"BYE"，这里只需要把通话状态设置成end
    if inbound_member_status == "BYE" or ext_member_status == "BYE":
        inbound_call_end(ext_number,callid, 0,'0','')
    elif ext_member_status == "RING":#callee处于响铃状态"memberstatus":"RING"
        inbound_call_ring(callid,inbound_caller_num,ext_number)
    elif ext_member_status == "ANSWER":#被叫分机接听来电"memberstatus":"ANSWER"
        inbound_call_answered(ext_number,callid)
        


def handle_inbound_call_events(list_members,callid):
    inbound_data = ''
    ext_data = ''
    inbound_member = ''
    counter = 0

    print('|Bitrix call| handle inbound calls')

    #遍历字典成员
    while counter < len(list_members):
        #处理inbound来电
        if list_members[counter].get('inbound'):
            inbound_member = list_members[counter]
            inbound_data = inbound_member['inbound']
        elif list_members[counter].get('ext'):#获取每个ext member信息进行上报
            ext_member = list_members[counter]
            ext_data = ext_member['ext']

        inbound_call_handler(callid,inbound_data,ext_data)
        counter += 1

def outbound_call_handler(callid,outbound_data,ext_data):

    outbound_callee_num = ''
    outbound_member_status = ''
    ext_number = ''

    #print('outbound call hander')

    if outbound_data != '':
        outbound_member_status = outbound_data['memberstatus']
        outbound_caller_num = outbound_data['from']
        outbound_callee_num = outbound_data['to']

    if ext_data != '':
        ext_member_status = ext_data['memberstatus']
        ext_number = ext_data['number']
    
    #检查主叫号码是否是CRM绑定的号码，不是则不用上报
    if check_callee_entity(ext_number) == False:
        print('|Bitrix user| not a bitrix user ', ext_number)
        return

    if outbound_member_status == 'RING':
        outbound_call_alert(callid,ext_number,outbound_callee_num)
    elif outbound_member_status == 'ANSWER':
        outbound_call_answered(ext_number,outbound_callee_num,callid)
    elif outbound_member_status == 'BYE' or ext_member_status == 'BYE':
        outbound_call_end(outbound_callee_num,ext_number,callid, 0,'0','')


def handle_outbound_call_events(list_members,callid):
    outbound_data = ''
    ext_data = ''
    outbound_member = ''
    counter = 0

    print('|Bitrix call| handle outbound calls')
    #遍历字典成员
    while counter < len(list_members):
        #处理inbound来电
        if list_members[counter].get('outbound'):
            outbound_member = list_members[counter]
            outbound_data = outbound_member['outbound']
        elif list_members[counter].get('ext'):#获取每个ext member信息进行上报
            ext_member = list_members[counter]
            ext_data = ext_member['ext']

        outbound_call_handler(callid,outbound_data,ext_data)
        counter += 1

def handle_inbound_newcdr_events(json_data):
    callid = json_data['callid']
    callee_num = json_data['callto']
    talk_duration = json_data['talkduraction']
    call_reason = json_data['status']
    
    #被叫号码 '4002'  '(4002)'
    #"callto":"6200(4002)"
    #"callto": "6200"
    if '(' in callee_num and ')' in callee_num:
        str1 = re.findall(r'[(](.*?)[)]',callee_num)
        callee_num = str1[0]

    print('|inbound new cdr| callid: ',callid)
    print('|inbound new cdr| callee_num: ',callee_num)
    print('|inbound new cdr| talk duration: ',talk_duration)
    print('|inbound new cdr| call reason: ',call_reason)
    print('|inbound new cdr| put call id to queue: ',callid)

    #进入VM不上报
    if call_reason == 'VOICEMAIL':
        return
    
    ret = inbound_call_end(callee_num, callid, 1, talk_duration,call_reason)
    if ret == -1:
        report_inbound_cancel_calls(callid)

    clear_call_queue.put(callid)


def handle_outbound_newcdr_events(json_data):
    callid = json_data['callid']
    caller_num = json_data['callfrom']
    callee_num = json_data['callto']
    talk_duration = json_data['talkduraction']
    call_reason = json_data['status']

    print('|outbound new cdr| callid: ',callid)
    print('|outbound new cdr| caller_num: ',caller_num)
    print('|outbound new cdr| callee_num: ',callee_num)
    print('|outbound new cdr| talk duration: ',talk_duration)
    print('|outbound new cdr| call reason: ',call_reason)
    print('|outbound new cdr| put call id to queue: ',callid)

    ret = outbound_call_end(callee_num,caller_num, callid, 1, talk_duration,call_reason)
    if ret == -1:
        report_inbound_cancel_calls(callid)

    clear_call_queue.put(callid)


def check_event_type(list_members):

    counter = 0
    while counter < len(list_members):
        if list_members[counter].get('inbound'):
            return 'inbound'
        elif list_members[counter].get('outbound'):
            return 'outbound'
        counter+=1

def api_events_handle(body):
    #通话事件处理
    callid = ''
    list_members = ''

    json_data = eval(body)
    if json_data['event'] == 'CallStatus':
        list_members = json_data['members']
        callid = json_data['callid']

        calltype = check_event_type(list_members)
        if calltype == 'inbound':
            handle_inbound_call_events(list_members,callid)
        elif calltype == 'outbound':
            handle_outbound_call_events(list_members,callid)

    elif json_data['event'] == 'NewCdr':
        cdr_call_type = json_data['type']
        if cdr_call_type == 'Inbound':
            handle_inbound_newcdr_events(json_data)
        elif cdr_call_type == 'Outbound':
            handle_outbound_newcdr_events(json_data)


def save_crm_userid():
    index = 0
    userinfo = ''
    result = ''
    internal_phone = ''
    userid = ''
    users_info = bitrix_userget()
    total_count = users_info['total']
    result = users_info['result']
    for userinfo in result:
        internal_phone = userinfo['UF_PHONE_INNER']
        userid = userinfo['ID']
        dict_user = {internal_phone:userid}
        dict_Bitrix_userid.update(dict_user)
        print('|Bitrix user| add user: ',dict_Bitrix_userid)

# a read thread, read data from remote
class Reader(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self)
        self.client = client
        
    def run(self):
        while True:
            data = self.client.recv(BUFSIZE)
            if(data):
                string = bytes.decode(data,encoding)
                #print(string)
                #print('receive new message: ')
                header,body = string.split('\r\n\r\n',1)
                #print(body, end='\n')
                event_queue.put(body)
                #api_events_handle(body)
            else:
                break
        #print("close:", self.client.getpeername())

# a listen thread, listen remote connect
# when a remote machine request to connect, it will create a read thread to handle
class Listener(threading.Thread):
    def __init__(self, name, port):
        threading.Thread.__init__(self)
        self.name = name
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        self.sock.listen(0)
    def run(self):
        #print("listener started")
        while True:
            client, cltadd = self.sock.accept()
            Reader(client).start()
            cltadd = cltadd
            #print("accept a connect")

def start_running():
    listen_thread  = Listener('listener', 8260)
    listen_thread.start()

def event_message_handle():
    #print('event message handler\n')
    while True:
        body = event_queue.get()
        #print(body)
        api_events_handle(body)
        event_queue.task_done()

class Clearcallthread (threading.Thread):
    def __init__(self, name):
        threading.Thread.__init__(self)
        self.name = name
    def run(self):
        delete_end_calls()

def delete_noneexist_calls():
    if(len(list_PBX_callid)):
        testcallid = list_PBX_callid[0]
        r_text = query_calldetails(testcallid)
        r_text = eval(r_text)
        if r_text['status'] == "Failed":
            if r_text['errno'] == "10005":
                delete_calls_bycallid(testcallid)

def delete_end_calls():
    print('event message handler\n')
    while True:
        callid = clear_call_queue.get()
        sleep(5)
        delete_calls_bycallid(callid)
        clear_call_queue.task_done()

        #存在PBX没有发newcdr的情况，主动查询通话是否存在 不存在自行删除
        delete_noneexist_calls()
    
def start_call_clear():
    call_clear_thread = Clearcallthread('call-clear')
    call_clear_thread.start()

def read_local_config():
    global pbx_url
    global bitrix_basic_url
    global api_username
    global api_password
    global basic_url

    print('read local configuration file')
    print('current working directory is: '+os.path.abspath(__file__))
    program_path = os.getcwd()
    
    if os.path.exists('config.txt') == False:
        print('No local configuration file')
        return

    with open('config.txt', 'r', encoding='utf-8') as f:
        for line in f.readlines():
            if len(line) == 1:
                continue
            key, value = line.split(':',1)
            #print('key: '+key)
            #print('value: '+value)
            if '#' in key:
                print('skip this config')
                continue

            if key == 'pbx_url' and value != '':
                pbx_url = value.strip('\n')#去除换行
                pbx_url = pbx_url.strip()#去除空格
                print('read from config pbx_url is: '+ pbx_url)
                basic_url = pbx_url + '/api/v2.0.0/'
            elif key == 'bitrix_basic_url' and value != '':
                bitrix_basic_url = value.strip('\n')
                bitrix_basic_url = bitrix_basic_url.strip()
                print('read from config bitrix_basic_url is: '+ bitrix_basic_url)
            elif key == 'api_username' and value != '':
                api_username = value.strip('\n')
                api_username = api_username.strip()
                print('read from config api_username is: '+ api_username)
            elif key == 'api_password' and value != '':
                api_password = value.strip('\n')
                api_password = api_password.strip()
                print('read from config api_password is: '+ api_password)


if __name__ == '__main__':

    read_local_config()
    #API连接
    api_login(api_username,api_password)

    #token刷新线程
    #start_keepalive_timer()

    #获取分机信息
    query_extensionlist()

    
    #获取CRM用户信息 根据CRM的internal phone number获取对应的userid
    save_crm_userid()

    #读取配置文件手动绑定CRM用户和分机--tbd


    #监听线程----接收数据存入队列
    start_running()

    #删除结束通话
    start_call_clear()

    #消息处理
    event_message_handle()


    
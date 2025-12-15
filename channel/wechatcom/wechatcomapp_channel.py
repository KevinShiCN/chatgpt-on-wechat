# -*- coding=utf-8 -*-
import io
import os
import time

import requests
import web
from wechatpy.enterprise import create_reply, parse_message
from wechatpy.enterprise.crypto import WeChatCrypto
from wechatpy.enterprise.exceptions import InvalidCorpIdException
from wechatpy.exceptions import InvalidSignatureException, WeChatClientException

from bridge.context import Context
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel
from channel.wechatcom.wechatcomapp_client import WechatComAppClient
from channel.wechatcom.wechatcomapp_message import WechatComAppMessage
from common.log import logger
from common.singleton import singleton
from common.utils import compress_imgfile, fsize, split_string_by_utf8_length, convert_webp_to_png, remove_markdown_symbol, split_markdown_by_length
from config import conf, subscribe_msg
from voice.audio_convert import any_to_amr, split_audio

MAX_UTF8_LEN = 2048


@singleton
class WechatComAppChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = []

    def __init__(self):
        super().__init__()
        self.corp_id = conf().get("wechatcom_corp_id")
        self.secret = conf().get("wechatcomapp_secret")
        self.agent_id = conf().get("wechatcomapp_agent_id")
        self.token = conf().get("wechatcomapp_token")
        self.aes_key = conf().get("wechatcomapp_aes_key")
        print(self.corp_id, self.secret, self.agent_id, self.token, self.aes_key)
        logger.info(
            "[wechatcom] init: corp_id: {}, secret: {}, agent_id: {}, token: {}, aes_key: {}".format(self.corp_id, self.secret, self.agent_id, self.token, self.aes_key)
        )
        self.crypto = WeChatCrypto(self.token, self.aes_key, self.corp_id)
        self.client = WechatComAppClient(self.corp_id, self.secret)

    def startup(self):
        # start message listener
        urls = ("/wxcomapp/?", "channel.wechatcom.wechatcomapp_channel.Query")
        app = web.application(urls, globals(), autoreload=False)
        port = conf().get("wechatcomapp_port", 9898)
        web.httpserver.runsimple(app.wsgifunc(), ("0.0.0.0", port))

    def _send_markdown(self, receiver, content):
        """发送 Markdown 消息"""
        try:
            # 使用企业微信的 markdown 消息类型
            url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={self.client.access_token}"
            data = {
                "touser": receiver,
                "msgtype": "markdown",
                "agentid": self.agent_id,
                "markdown": {
                    "content": content
                }
            }
            response = requests.post(url, json=data)
            result = response.json()
            if result.get("errcode") != 0:
                logger.error(f"[wechatcom] send markdown failed: {result}")
                # 如果 markdown 发送失败，降级为文本消息
                plain_text = remove_markdown_symbol(content)
                self.client.message.send_text(self.agent_id, receiver, plain_text)
            else:
                logger.debug(f"[wechatcom] send markdown success: {result}")
        except Exception as e:
            logger.error(f"[wechatcom] send markdown error: {e}")
            # 发生异常时降级为文本消息
            plain_text = remove_markdown_symbol(content)
            self.client.message.send_text(self.agent_id, receiver, plain_text)

    def send(self, reply: Reply, context: Context):
        receiver = context["receiver"]
        if reply.type in [ReplyType.TEXT, ReplyType.ERROR, ReplyType.INFO]:
            # 保留原始内容用于发送 Markdown
            reply_content = reply.content
            # 使用智能切分，避免在 HTML 标签中间切断
            texts = split_markdown_by_length(reply_content, MAX_UTF8_LEN)
            if len(texts) > 1:
                logger.info("[wechatcom] markdown too long, split into {} parts".format(len(texts)))
            for i, text in enumerate(texts):
                # 使用 Markdown 消息类型
                self._send_markdown(receiver, text)
                if i != len(texts) - 1:
                    time.sleep(0.5)  # 休眠0.5秒，防止发送过快乱序
            logger.info("[wechatcom] Do send markdown to {}: {} chars".format(receiver, len(reply_content)))
        elif reply.type == ReplyType.VOICE:
            try:
                media_ids = []
                file_path = reply.content
                amr_file = os.path.splitext(file_path)[0] + ".amr"
                any_to_amr(file_path, amr_file)
                duration, files = split_audio(amr_file, 60 * 1000)
                if len(files) > 1:
                    logger.info("[wechatcom] voice too long {}s > 60s , split into {} parts".format(duration / 1000.0, len(files)))
                for path in files:
                    response = self.client.media.upload("voice", open(path, "rb"))
                    logger.debug("[wechatcom] upload voice response: {}".format(response))
                    media_ids.append(response["media_id"])
            except WeChatClientException as e:
                logger.error("[wechatcom] upload voice failed: {}".format(e))
                return
            try:
                os.remove(file_path)
                if amr_file != file_path:
                    os.remove(amr_file)
            except Exception:
                pass
            for media_id in media_ids:
                self.client.message.send_voice(self.agent_id, receiver, media_id)
                time.sleep(1)
            logger.info("[wechatcom] sendVoice={}, receiver={}".format(reply.content, receiver))
        elif reply.type == ReplyType.IMAGE_URL:  # 从网络下载图片
            img_url = reply.content
            pic_res = requests.get(img_url, stream=True)
            image_storage = io.BytesIO()
            for block in pic_res.iter_content(1024):
                image_storage.write(block)
            sz = fsize(image_storage)
            if sz >= 10 * 1024 * 1024:
                logger.info("[wechatcom] image too large, ready to compress, sz={}".format(sz))
                image_storage = compress_imgfile(image_storage, 10 * 1024 * 1024 - 1)
                logger.info("[wechatcom] image compressed, sz={}".format(fsize(image_storage)))
            image_storage.seek(0)
            if ".webp" in img_url:
                try:
                    image_storage = convert_webp_to_png(image_storage)
                except Exception as e:
                    logger.error(f"Failed to convert image: {e}")
                    return
            try:
                response = self.client.media.upload("image", image_storage)
                logger.debug("[wechatcom] upload image response: {}".format(response))
            except WeChatClientException as e:
                logger.error("[wechatcom] upload image failed: {}".format(e))
                return

            self.client.message.send_image(self.agent_id, receiver, response["media_id"])
            logger.info("[wechatcom] sendImage url={}, receiver={}".format(img_url, receiver))
        elif reply.type == ReplyType.IMAGE:  # 从文件读取图片
            image_storage = reply.content
            sz = fsize(image_storage)
            if sz >= 10 * 1024 * 1024:
                logger.info("[wechatcom] image too large, ready to compress, sz={}".format(sz))
                image_storage = compress_imgfile(image_storage, 10 * 1024 * 1024 - 1)
                logger.info("[wechatcom] image compressed, sz={}".format(fsize(image_storage)))
            image_storage.seek(0)
            try:
                response = self.client.media.upload("image", image_storage)
                logger.debug("[wechatcom] upload image response: {}".format(response))
            except WeChatClientException as e:
                logger.error("[wechatcom] upload image failed: {}".format(e))
                return
            self.client.message.send_image(self.agent_id, receiver, response["media_id"])
            logger.info("[wechatcom] sendImage, receiver={}".format(receiver))


class Query:
    def GET(self):
        channel = WechatComAppChannel()
        params = web.input()
        logger.info("[wechatcom] receive params: {}".format(params))
        try:
            signature = params.msg_signature
            timestamp = params.timestamp
            nonce = params.nonce
            echostr = params.echostr
            echostr = channel.crypto.check_signature(signature, timestamp, nonce, echostr)
        except InvalidSignatureException:
            raise web.Forbidden()
        return echostr

    def POST(self):
        channel = WechatComAppChannel()
        params = web.input()
        logger.info("[wechatcom] receive params: {}".format(params))
        try:
            signature = params.msg_signature
            timestamp = params.timestamp
            nonce = params.nonce
            message = channel.crypto.decrypt_message(web.data(), signature, timestamp, nonce)
        except (InvalidSignatureException, InvalidCorpIdException):
            raise web.Forbidden()
        msg = parse_message(message)
        logger.debug("[wechatcom] receive message: {}, msg= {}".format(message, msg))
        if msg.type == "event":
            logger.debug(f"[wechatcom] receive event: {msg.event}")  # event 用 debug 级别，避免刷屏
            if msg.event == "subscribe":
                pass
                # reply_content = subscribe_msg()
                # if reply_content:
                #     reply = create_reply(reply_content, msg).render()
                #     res = channel.crypto.encrypt_message(reply, nonce, timestamp)
                #     return res
        else:
            try:
                wechatcom_msg = WechatComAppMessage(msg, client=channel.client)
            except NotImplementedError as e:
                logger.info("[wechatcom] Unsupported message type: " + str(e))  # 改为 info 级别
                return "success"
            context = channel._compose_context(
                wechatcom_msg.ctype,
                wechatcom_msg.content,
                isgroup=False,
                msg=wechatcom_msg,
            )
            if context:
                channel.produce(context)
        return "success"

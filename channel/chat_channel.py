import os
import re
import threading
import time
from asyncio import CancelledError
from concurrent.futures import Future, ThreadPoolExecutor

from bridge.context import *
from bridge.reply import *
from channel.channel import Channel
from common.dequeue import Dequeue
from common import memory
from common.error_notify import notify_channel_error
from plugins import *

try:
    from voice.audio_convert import any_to_wav
except Exception as e:
    pass

handler_pool = ThreadPoolExecutor(max_workers=8)  # 处理消息的线程池


# 消息去重缓存（防止重复处理同一条消息）
processed_messages = {}  # {msg_id: timestamp}
MESSAGE_EXPIRY = 300  # 消息ID缓存过期时间（秒）

# 待处理的文本消息（等待可能的图片）
pending_text_messages = {}  # {session_id: {"context": context, "time": timestamp, "future": future, "reply": reply, "cancelled": False}}
TEXT_WAIT_TIME = 10  # 等待图片的时间（秒）
IMAGE_WAIT_TIME = 20  # 每张图片之间的等待时间（秒），收到新图片会重置倒计时

# 待处理的生图消息（等待可能的参考图片）
pending_image_create = {}  # {session_id: {"context": context, "time": timestamp, "future": future, "result": result, "cancelled": False, "ref_images": []}}



def cleanup_expired_cache():
    """清理过期的缓存"""
    current_time = time.time()
    # 清理消息ID缓存
    expired_msgs = [msg_id for msg_id, timestamp in processed_messages.items()
                    if current_time - timestamp > MESSAGE_EXPIRY]
    for msg_id in expired_msgs:
        del processed_messages[msg_id]


# 抽象类, 它包含了与消息通道无关的通用处理逻辑
class ChatChannel(Channel):
    name = None  # 登录的用户名
    user_id = None  # 登录的用户id
    futures = {}  # 记录每个session_id提交到线程池的future对象, 用于重置会话时把没执行的future取消掉，正在执行的不会被取消
    sessions = {}  # 用于控制并发，每个session_id同时只能有一个context在处理
    lock = threading.Lock()  # 用于控制对sessions的访问

    def __init__(self):
        _thread = threading.Thread(target=self.consume)
        _thread.setDaemon(True)
        _thread.start()

    def _parse_gacha_command(self, content):
        """
        解析抽卡命令
        格式: [次数] [比例(可选)] [提示词]

        示例:
        - "10 16:9 一只猫" → (10, "16:9 一只猫")
        - "3 美丽风景"     → (3, "美丽风景")
        - "风景"          → (默认次数, "风景")

        返回: (count, prompt_with_ratio)
        """
        content = content.strip()
        if not content:
            return conf().get("gacha_default_count", 3), ""

        # 匹配开头的数字（抽卡次数）
        match = re.match(r'^(\d+)\s*(.*)$', content)

        if match:
            count = int(match.group(1))
            prompt_with_ratio = match.group(2).strip()
        else:
            count = conf().get("gacha_default_count", 3)
            prompt_with_ratio = content

        # 限制最大次数
        max_count = conf().get("gacha_max_count", 20)
        if count > max_count:
            logger.warning(f"[chat_channel] gacha count {count} exceeds max {max_count}, limiting to {max_count}")
            count = max_count

        # 确保至少1次
        if count < 1:
            count = 1

        return count, prompt_with_ratio

    # 根据消息构造context，消息内容相关的触发项写在这里
    def _compose_context(self, ctype: ContextType, content, **kwargs):
        context = Context(ctype, content)
        context.kwargs = kwargs
        # context首次传入时，origin_ctype是None,
        # 引入的起因是：当输入语音时，会嵌套生成两个context，第一步语音转文本，第二步通过文本生成文字回复。
        # origin_ctype用于第二步文本回复时，判断是否需要匹配前缀，如果是私聊的语音，就不需要匹配前缀
        if "origin_ctype" not in context:
            context["origin_ctype"] = ctype
        # context首次传入时，receiver是None，根据类型设置receiver
        first_in = "receiver" not in context
        # 群名匹配过程，设置session_id和receiver
        if first_in:  # context首次传入时，receiver是None，根据类型设置receiver
            config = conf()
            cmsg = context["msg"]
            user_data = conf().get_user_data(cmsg.from_user_id)
            context["openai_api_key"] = user_data.get("openai_api_key")
            context["gpt_model"] = user_data.get("gpt_model")
            if context.get("isgroup", False):
                group_name = cmsg.other_user_nickname
                group_id = cmsg.other_user_id

                group_name_white_list = config.get("group_name_white_list", [])
                group_name_keyword_white_list = config.get("group_name_keyword_white_list", [])
                if any(
                    [
                        group_name in group_name_white_list,
                        "ALL_GROUP" in group_name_white_list,
                        check_contain(group_name, group_name_keyword_white_list),
                    ]
                ):
                    group_chat_in_one_session = conf().get("group_chat_in_one_session", [])
                    session_id = cmsg.actual_user_id
                    if any(
                        [
                            group_name in group_chat_in_one_session,
                            "ALL_GROUP" in group_chat_in_one_session,
                        ]
                    ):
                        session_id = group_id
                else:
                    logger.debug(f"No need reply, groupName not in whitelist, group_name={group_name}")
                    return None
                context["session_id"] = session_id
                context["receiver"] = group_id
            else:
                context["session_id"] = cmsg.other_user_id
                context["receiver"] = cmsg.other_user_id
            e_context = PluginManager().emit_event(EventContext(Event.ON_RECEIVE_MESSAGE, {"channel": self, "context": context}))
            context = e_context["context"]
            if e_context.is_pass() or context is None:
                return context
            if cmsg.from_user_id == self.user_id and not config.get("trigger_by_self", True):
                logger.debug("[chat_channel]self message skipped")
                return None

        # 消息内容匹配过程，并处理content
        if ctype == ContextType.TEXT:
            if first_in and "」\n- - - - - - -" in content:  # 初次匹配 过滤引用消息
                logger.debug(content)
                logger.debug("[chat_channel]reference query skipped")
                return None

            nick_name_black_list = conf().get("nick_name_black_list", [])
            if context.get("isgroup", False):  # 群聊
                # 校验关键字
                match_prefix = check_prefix(content, conf().get("group_chat_prefix"))
                match_contain = check_contain(content, conf().get("group_chat_keyword"))
                flag = False
                if context["msg"].to_user_id != context["msg"].actual_user_id:
                    if match_prefix is not None or match_contain is not None:
                        flag = True
                        if match_prefix:
                            content = content.replace(match_prefix, "", 1).strip()
                    if context["msg"].is_at:
                        nick_name = context["msg"].actual_user_nickname
                        if nick_name and nick_name in nick_name_black_list:
                            # 黑名单过滤
                            logger.warning(f"[chat_channel] Nickname {nick_name} in In BlackList, ignore")
                            return None

                        logger.info("[chat_channel]receive group at")
                        if not conf().get("group_at_off", False):
                            flag = True
                        self.name = self.name if self.name is not None else ""  # 部分渠道self.name可能没有赋值
                        pattern = f"@{re.escape(self.name)}(\u2005|\u0020)"
                        subtract_res = re.sub(pattern, r"", content)
                        if isinstance(context["msg"].at_list, list):
                            for at in context["msg"].at_list:
                                pattern = f"@{re.escape(at)}(\u2005|\u0020)"
                                subtract_res = re.sub(pattern, r"", subtract_res)
                        if subtract_res == content and context["msg"].self_display_name:
                            # 前缀移除后没有变化，使用群昵称再次移除
                            pattern = f"@{re.escape(context['msg'].self_display_name)}(\u2005|\u0020)"
                            subtract_res = re.sub(pattern, r"", content)
                        content = subtract_res
                if not flag:
                    if context["origin_ctype"] == ContextType.VOICE:
                        logger.info("[chat_channel]receive group voice, but checkprefix didn't match")
                    return None
            else:  # 单聊
                nick_name = context["msg"].from_user_nickname
                if nick_name and nick_name in nick_name_black_list:
                    # 黑名单过滤
                    logger.warning(f"[chat_channel] Nickname '{nick_name}' in In BlackList, ignore")
                    return None

                match_prefix = check_prefix(content, conf().get("single_chat_prefix", [""]))
                if match_prefix is not None:  # 判断如果匹配到自定义前缀，则返回过滤掉前缀+空格后的内容
                    content = content.replace(match_prefix, "", 1).strip()
                elif context["origin_ctype"] == ContextType.VOICE:  # 如果源消息是私聊的语音消息，允许不匹配前缀，放宽条件
                    pass
                else:
                    logger.info("[chat_channel]receive single chat msg, but checkprefix didn't match")
                    return None
            content = content.strip()
            # 抽卡前缀匹配（优先于普通生图）
            gacha_match_prefix = check_prefix(content, conf().get("gacha_prefix") or ["抽卡"])
            if gacha_match_prefix:
                content = content.replace(gacha_match_prefix, "", 1).strip()
                # 解析抽卡次数和提示词
                gacha_count, gacha_prompt = self._parse_gacha_command(content)
                context.type = ContextType.GACHA_CREATE
                context["gacha_count"] = gacha_count
                context.content = gacha_prompt
            else:
                img_match_prefix = check_prefix(content, conf().get("image_create_prefix",[""]))
                if img_match_prefix:
                    content = content.replace(img_match_prefix, "", 1)
                    context.type = ContextType.IMAGE_CREATE
                else:
                    context.type = ContextType.TEXT
                context.content = content.strip()
            if "desire_rtype" not in context and conf().get("always_reply_voice") and ReplyType.VOICE not in self.NOT_SUPPORT_REPLYTYPE:
                context["desire_rtype"] = ReplyType.VOICE
        elif context.type == ContextType.VOICE:
            if "desire_rtype" not in context and conf().get("voice_reply_voice") and ReplyType.VOICE not in self.NOT_SUPPORT_REPLYTYPE:
                context["desire_rtype"] = ReplyType.VOICE
        return context

    def _handle(self, context: Context):
        if context is None:
            return
        # IMAGE_CREATE和GACHA_CREATE类型允许空content（用户可能只发送前缀，等待后续图片）
        if not context.content and context.type not in [ContextType.IMAGE_CREATE, ContextType.GACHA_CREATE]:
            return

        # 消息去重检查
        msg_id = None
        if context.get("msg"):
            msg_id = getattr(context["msg"], "msg_id", None) or getattr(context["msg"], "id", None)

        if msg_id:
            # 检查是否已处理过
            if msg_id in processed_messages:
                logger.warning(f"[chat_channel] duplicate message detected, skip: {msg_id}")
                return

            # 记录消息ID
            processed_messages[msg_id] = time.time()

            # 定期清理过期缓存（每100条消息清理一次）
            if len(processed_messages) % 100 == 0:
                cleanup_expired_cache()

        logger.debug("[chat_channel] ready to handle context: {}".format(context))

        # 获取重试次数配置，默认为2次
        max_retry = conf().get("empty_reply_retry_count", 2)
        retry_count = 0
        reply = None

        # 重试逻辑
        while retry_count <= max_retry:
            # reply的构建步骤
            reply = self._generate_reply(context)

            # 如果reply有内容，直接跳出循环
            if reply and reply.content:
                break

            # IMAGE_CREATE和GACHA_CREATE类型使用异步处理，空reply是正常的，不需要重试
            if context.type in [ContextType.IMAGE_CREATE, ContextType.GACHA_CREATE]:
                break

            # 如果TEXT消息更新了pending_image_create，空reply是正常的，不需要重试
            session_id = context.get("session_id")
            if context.type == ContextType.TEXT and session_id and session_id in pending_image_create:
                logger.info("[chat_channel] TEXT message updated IMAGE_CREATE prompt, no retry needed")
                break

            # 如果TEXT消息已经提交到pending_text_messages进行异步处理，不需要重试
            if context.type == ContextType.TEXT and session_id and session_id in pending_text_messages:
                logger.info("[chat_channel] TEXT message submitted for async processing, no retry needed")
                break

            # 如果IMAGE消息添加了ref_images到pending_image_create，空reply是正常的，不需要重试
            if context.type == ContextType.IMAGE and session_id and session_id in pending_image_create:
                logger.info("[chat_channel] IMAGE added to IMAGE_CREATE task, no retry needed")
                break

            # 如果IMAGE消息添加到了pending_text_messages，空reply是正常的，不需要重试
            if context.type == ContextType.IMAGE and session_id and session_id in pending_text_messages:
                logger.info("[chat_channel] IMAGE added to TEXT task, no retry needed")
                break

            # 如果没有内容且还有重试次数
            if retry_count < max_retry:
                retry_count += 1
                logger.warning(f"[chat_channel] reply is empty, retrying... ({retry_count}/{max_retry})")
                time.sleep(2 * retry_count)  # 递增延迟：2秒、4秒、6秒...
            else:
                # 已达到最大重试次数
                retry_count += 1
                break

        logger.debug("[chat_channel] ready to decorate reply: {}".format(reply))

        # reply的包装步骤
        if reply and reply.content:
            reply = self._decorate_reply(context, reply)

            # reply的发送步骤
            self._send_reply(context, reply)
        else:
            # IMAGE_CREATE和GACHA_CREATE类型使用异步处理，空reply是正常的，不发送错误消息
            if context.type in [ContextType.IMAGE_CREATE, ContextType.GACHA_CREATE]:
                logger.info(f"[chat_channel] {context.type} async processing, no immediate reply")
            # IMAGE类型如果被添加到pending_image_create，也不发送错误消息
            elif context.type == ContextType.IMAGE and context.get("session_id") in pending_image_create:
                logger.info("[chat_channel] IMAGE added to IMAGE_CREATE, no error reply needed")
            # IMAGE类型如果被添加到pending_text_messages，也不发送错误消息
            elif context.type == ContextType.IMAGE and context.get("session_id") in pending_text_messages:
                logger.info("[chat_channel] IMAGE added to TEXT task, no error reply needed")
            # TEXT类型如果更新了pending_image_create，也不发送错误消息
            elif context.type == ContextType.TEXT and context.get("session_id") in pending_image_create:
                logger.info("[chat_channel] TEXT updated IMAGE_CREATE prompt, no error reply needed")
            # TEXT类型如果已经提交到pending_text_messages进行异步处理，也不发送错误消息
            elif context.type == ContextType.TEXT and context.get("session_id") in pending_text_messages:
                logger.info("[chat_channel] TEXT submitted for async processing, no error reply needed")
            else:
                # 处理空回复的情况,给用户明确的反馈
                logger.error(f"[chat_channel] reply is empty after {retry_count} attempts, context: {context}")
                error_reply = Reply(ReplyType.ERROR, f"抱歉,我尝试了 {retry_count} 次但仍无法生成回复,请稍后再试")
                error_reply = self._decorate_reply(context, error_reply)
                self._send_reply(context, error_reply)

    def _generate_reply(self, context: Context, reply: Reply = Reply()) -> Reply:
        e_context = PluginManager().emit_event(
            EventContext(
                Event.ON_HANDLE_CONTEXT,
                {"channel": self, "context": context, "reply": reply},
            )
        )
        reply = e_context["reply"]
        if not e_context.is_pass():
            logger.debug("[chat_channel] ready to handle context: type={}, content={}".format(context.type, context.content))
            if context.type == ContextType.TEXT or context.type == ContextType.IMAGE_CREATE:  # 文字和图片消息
                if context.type == ContextType.TEXT:
                    # 文本消息延迟处理，等待可能的图片
                    session_id = context.get("session_id")

                    # 检查是否有待处理的IMAGE_CREATE（用户发送了"生图"后又发送文本描述）
                    if session_id and session_id in pending_image_create:
                        # 更新IMAGE_CREATE的prompt内容
                        pending = pending_image_create[session_id]
                        old_content = pending["context"].content
                        new_content = context.content if not old_content else f"{old_content} {context.content}"
                        pending["context"].content = new_content
                        logger.info(f"[chat_channel] updated IMAGE_CREATE prompt: {new_content[:50]}...")
                        # 不继续处理这条文本消息，等待图片
                        return Reply()

                    # 检查是否有待处理的图片消息正在等待文本
                    if session_id and session_id in pending_text_messages:
                        # 有待处理的文本，说明这是新的文本消息，取消之前的
                        old_pending = pending_text_messages[session_id]
                        old_pending["cancelled"] = True  # 设置取消标志，让旧任务知道被取消了
                        if "future" in old_pending and not old_pending["future"].done():
                            old_pending["future"].cancel()
                            logger.info("[chat_channel] cancelled previous pending text message")

                    # 立即开始处理文本消息（异步）
                    logger.info(f"[chat_channel] text message received, start processing immediately and wait for possible image...")

                    def immediate_text_handler():
                        # 保存对自己数据的引用（重要！防止被新任务覆盖后读取错误数据）
                        my_pending = pending_text_messages.get(session_id)
                        if not my_pending:
                            logger.info("[chat_channel] TEXT task data not found, skip")
                            return

                        # 立即处理文本消息
                        pending_context = context
                        pending_context["channel"] = e_context["channel"]

                        logger.info("[chat_channel] processing text message immediately...")
                        text_reply = super(ChatChannel, self).build_reply_content(pending_context.content, pending_context)

                        # 保存处理结果到自己的数据中
                        my_pending["reply"] = text_reply

                        # 动态等待：初始等待10秒，收到图片后重置为20秒
                        logger.info(f"[chat_channel] waiting for possible image (initial {TEXT_WAIT_TIME}s, then {IMAGE_WAIT_TIME}s after each image)...")
                        time.sleep(TEXT_WAIT_TIME)  # 初始等待10秒

                        # 动态等待循环：检查是否有新图片
                        max_total_wait = 180  # 最大总等待时间（秒）
                        total_waited = TEXT_WAIT_TIME
                        while total_waited < max_total_wait:
                            # 检查是否被取消（使用自己的数据引用）
                            if my_pending.get("cancelled", False):
                                logger.info("[chat_channel] TEXT task was cancelled during waiting, will process existing images")
                                break  # 退出等待，但继续处理已有的图片

                            last_image_time = my_pending.get("last_image_time")
                            if last_image_time:
                                time_since_last_image = time.time() - last_image_time
                                # 如果距离最后一张图片超过20秒，开始处理
                                if time_since_last_image >= IMAGE_WAIT_TIME:
                                    logger.info(f"[chat_channel] {IMAGE_WAIT_TIME}s passed since last image, processing...")
                                    break
                                # 否则继续等待
                                remaining = IMAGE_WAIT_TIME - time_since_last_image
                                logger.debug(f"[chat_channel] waiting for more images, {remaining:.1f}s remaining...")
                                time.sleep(2)
                                total_waited += 2
                            else:
                                # 没有收到图片，退出等待
                                break

                        # 使用自己的数据（不再从 pending_text_messages[session_id] 读取）
                        images = my_pending.get("images", [])

                        if images:
                            # 有图片，进行图片+文本组合处理
                            logger.info(f"[chat_channel] found {len(images)} images, processing with text query...")
                            text_query = pending_context.content

                            # 清理pending（只有当这个任务还是当前任务时才删除）
                            if pending_text_messages.get(session_id) is my_pending:
                                del pending_text_messages[session_id]

                            # 对每张图片进行识别，带上文本问题
                            for i, img_path in enumerate(images):
                                logger.info(f"[chat_channel] processing image {i+1}/{len(images)} with query: {text_query[:30]}...")
                                # 构造图片识别的context
                                img_context = Context(ContextType.IMAGE, img_path)
                                img_context.kwargs = pending_context.kwargs.copy()
                                img_context["img_query"] = text_query
                                img_context["channel"] = pending_context.get("channel")

                                # 调用图片识别
                                img_reply = super(ChatChannel, self).build_reply_content(img_path, img_context)

                                if img_reply and img_reply.content:
                                    img_reply = self._decorate_reply(img_context, img_reply)
                                    self._send_reply(img_context, img_reply)
                        else:
                            # 没有图片
                            if my_pending.get("cancelled", False):
                                # 被取消且没有图片，不发送回复
                                logger.info("[chat_channel] TEXT task was cancelled with no images, skip sending")
                            else:
                                # 没有被取消，发送文本回复
                                logger.info("[chat_channel] no image received within timeout, sending text reply")
                                # 清理pending（只有当这个任务还是当前任务时才删除）
                                if pending_text_messages.get(session_id) is my_pending:
                                    del pending_text_messages[session_id]

                                if text_reply and text_reply.content:
                                    text_reply = self._decorate_reply(pending_context, text_reply)
                                    self._send_reply(pending_context, text_reply)

                    # 保存待处理的文本消息
                    pending_text_messages[session_id] = {
                        "context": context,
                        "time": time.time(),
                        "reply": None,
                        "cancelled": False,
                        "images": [],  # 收到的图片列表
                        "last_image_time": None  # 最后收到图片的时间
                    }

                    # 提交立即处理任务
                    future = handler_pool.submit(immediate_text_handler)
                    pending_text_messages[session_id]["future"] = future

                    # 不返回 reply，由异步任务处理
                    return Reply()
                else:
                    # IMAGE_CREATE 类型延迟处理，等待可能的参考图片
                    session_id = context.get("session_id")
                    
                    # 检查是否配置了 nano-banana 生图模型
                    kgapi_model = conf().get("kgapi_image_model", "")
                    if not kgapi_model or not kgapi_model.startswith("nano-banana"):
                        # 未配置 nano-banana 模型，使用原有逻辑
                        context["channel"] = e_context["channel"]
                        reply = super().build_reply_content(context.content, context)
                    else:
                        # 使用KGAPI，延迟处理等待参考图片
                        logger.info(f"[chat_channel] IMAGE_CREATE received, start processing and wait {TEXT_WAIT_TIME}s for possible reference image...")
                        
                        def immediate_image_create_handler():
                            from bot.kgapi.kgapi_image import KGAPIImage
                            from concurrent.futures import ThreadPoolExecutor, as_completed
                            kgapi = KGAPIImage()

                            # 动态等待：每收到一张图片就重置倒计时
                            # 初始等待10秒，之后每次检查距离最后一张图片是否超过20秒
                            logger.info(f"[chat_channel] waiting for user input (initial {TEXT_WAIT_TIME}s, then {IMAGE_WAIT_TIME}s after each image)...")
                            time.sleep(TEXT_WAIT_TIME)  # 初始等待10秒

                            # 动态等待循环：检查是否有新图片
                            max_total_wait = 180  # 最大总等待时间（秒），防止无限等待
                            total_waited = TEXT_WAIT_TIME
                            while total_waited < max_total_wait:
                                if session_id not in pending_image_create:
                                    logger.info("[chat_channel] IMAGE_CREATE task was cancelled or removed")
                                    return

                                pending = pending_image_create[session_id]
                                last_image_time = pending.get("last_image_time", pending["time"])
                                time_since_last_image = time.time() - last_image_time

                                # 如果距离最后一张图片超过20秒，开始处理
                                if time_since_last_image >= IMAGE_WAIT_TIME:
                                    logger.info(f"[chat_channel] {IMAGE_WAIT_TIME}s passed since last image, starting processing...")
                                    break

                                # 否则继续等待，每2秒检查一次
                                remaining = IMAGE_WAIT_TIME - time_since_last_image
                                logger.debug(f"[chat_channel] waiting for more images, {remaining:.1f}s remaining...")
                                time.sleep(2)
                                total_waited += 2

                            # 检查pending_image_create的状态
                            if session_id not in pending_image_create:
                                logger.info("[chat_channel] IMAGE_CREATE task was cancelled or removed")
                                return

                            pending = pending_image_create[session_id]
                            ref_images = pending.get("ref_images", [])
                            final_content = pending["context"].content

                            # 根据收到的内容决定使用哪种API
                            ok, result = None, None

                            # 定期提醒功能
                            reminder_stop = threading.Event()
                            def send_reminder():
                                """每60秒发送一次进度提醒"""
                                minute = 1
                                while not reminder_stop.is_set():
                                    if reminder_stop.wait(60):  # 等待60秒或被停止
                                        break
                                    if not reminder_stop.is_set():
                                        reminder_msg = f"正在生图中，已等待{minute}分钟，请继续耐心等待..."
                                        reminder_reply = Reply(ReplyType.TEXT, reminder_msg)
                                        reminder_reply = self._decorate_reply(context, reminder_reply)
                                        self._send_reply(context, reminder_reply)
                                        logger.info(f"[chat_channel] sent reminder: {minute} minute(s) elapsed")
                                        minute += 1

                            # 如果有参考图片，使用图生图
                            if ref_images:
                                logger.info(f"[chat_channel] found {len(ref_images)} reference images, using edit mode")
                                if final_content and final_content.strip():
                                    # 发送提示消息
                                    tip_msg = f"收到了{len(ref_images)}张图片，以及提示词：{final_content}\n正在为您生图，请等待1-2分钟，如有问题请联系管理员干饭CEO"
                                    tip_reply = Reply(ReplyType.TEXT, tip_msg)
                                    tip_reply = self._decorate_reply(context, tip_reply)
                                    self._send_reply(context, tip_reply)
                                    # 启动提醒线程
                                    reminder_thread = threading.Thread(target=send_reminder, daemon=True)
                                    reminder_thread.start()
                                    # 调用图生图API
                                    ok, result = kgapi.edit_img(final_content, ref_images)
                                    # 停止提醒线程
                                    reminder_stop.set()
                                else:
                                    logger.warning("[chat_channel] IMAGE_CREATE with reference images but no description")
                                    ok, result = False, "请提供图片描述"
                            # 如果没有参考图片但有描述，使用文生图
                            elif final_content and final_content.strip():
                                logger.info("[chat_channel] no reference image received, using text-to-image")
                                # 发送提示消息
                                tip_msg = f"收到了提示词：{final_content}\n正在为您生图，请等待1-2分钟，如有问题请联系管理员干饭CEO"
                                tip_reply = Reply(ReplyType.TEXT, tip_msg)
                                tip_reply = self._decorate_reply(context, tip_reply)
                                self._send_reply(context, tip_reply)
                                # 启动提醒线程
                                reminder_thread = threading.Thread(target=send_reminder, daemon=True)
                                reminder_thread.start()
                                # 调用文生图API
                                ok, result = kgapi.create_img(final_content)
                                # 停止提醒线程
                                reminder_stop.set()
                            else:
                                # 既没有图片也没有描述，不发送任何消息
                                logger.info("[chat_channel] IMAGE_CREATE timeout with no content or images, skipping")
                                del pending_image_create[session_id]
                                return

                            # 清理待处理队列
                            del pending_image_create[session_id]

                            # 发送结果
                            if ok:
                                img_reply = Reply(ReplyType.IMAGE_URL, result)
                            else:
                                img_reply = Reply(ReplyType.ERROR, result)

                            img_reply = self._decorate_reply(context, img_reply)
                            self._send_reply(context, img_reply)
                        
                        # 检查是否有最近的图片（用户可能先发图片后发"生图"文本）
                        recent_images = []
                        if session_id in memory.USER_IMAGE_CACHE:
                            cached = memory.USER_IMAGE_CACHE[session_id]
                            # 检查图片是否在10秒内
                            if time.time() - cached.get("time", 0) < TEXT_WAIT_TIME:
                                img_path = cached.get("path")
                                if img_path:
                                    recent_images.append(img_path)
                                    logger.info(f"[chat_channel] found recent image from cache: {img_path}")

                        # 保存待处理的生图消息
                        pending_image_create[session_id] = {
                            "context": context,
                            "time": time.time(),
                            "result": None,
                            "cancelled": False,
                            "ref_images": recent_images  # 包含最近的图片
                        }

                        context["channel"] = e_context["channel"]

                        # 提交异步处理任务
                        future = handler_pool.submit(immediate_image_create_handler)
                        pending_image_create[session_id]["future"] = future

                        # 不返回 reply，由异步任务处理
                        return Reply()
            elif context.type == ContextType.GACHA_CREATE:  # 抽卡生图（批量生成）
                session_id = context.get("session_id")
                gacha_count = context.get("gacha_count", conf().get("gacha_default_count", 3))

                # 检查是否配置了 nano-banana 生图模型
                kgapi_model = conf().get("kgapi_image_model", "")
                if not kgapi_model or not kgapi_model.startswith("nano-banana"):
                    # 未配置 nano-banana 模型，提示用户
                    logger.warning("[chat_channel] GACHA_CREATE requires nano-banana model")
                    return Reply(ReplyType.ERROR, "抽卡功能需要配置 nano-banana 生图模型")

                logger.info(f"[chat_channel] GACHA_CREATE received, count={gacha_count}, start processing...")

                def gacha_image_create_handler():
                    from bot.kgapi.kgapi_image import KGAPIImage
                    kgapi = KGAPIImage()

                    # 动态等待参考图片（复用现有机制）
                    logger.info(f"[chat_channel] GACHA waiting for user input (initial {TEXT_WAIT_TIME}s)...")
                    time.sleep(TEXT_WAIT_TIME)

                    # 动态等待循环
                    max_total_wait = 180
                    total_waited = TEXT_WAIT_TIME
                    while total_waited < max_total_wait:
                        if session_id not in pending_image_create:
                            logger.info("[chat_channel] GACHA task was cancelled or removed")
                            return

                        pending = pending_image_create[session_id]
                        last_image_time = pending.get("last_image_time", pending["time"])
                        time_since_last_image = time.time() - last_image_time

                        if time_since_last_image >= IMAGE_WAIT_TIME:
                            logger.info(f"[chat_channel] GACHA {IMAGE_WAIT_TIME}s passed since last image, starting...")
                            break

                        time.sleep(2)
                        total_waited += 2

                    if session_id not in pending_image_create:
                        logger.info("[chat_channel] GACHA task was cancelled or removed")
                        return

                    pending = pending_image_create[session_id]
                    ref_images = pending.get("ref_images", [])
                    final_content = pending["context"].content
                    gacha_count_final = pending["context"].get("gacha_count", gacha_count)

                    # 检查是否有内容
                    if not final_content and not ref_images:
                        logger.info("[chat_channel] GACHA timeout with no content or images, skipping")
                        del pending_image_create[session_id]
                        return

                    # 发送开始提示
                    mode_text = "图生图" if ref_images else "文生图"
                    start_msg = f"开始抽卡，共{gacha_count_final}张（{mode_text}模式），请耐心等待...\n提示词：{final_content}"
                    if ref_images:
                        start_msg += f"\n参考图片：{len(ref_images)}张"
                    start_reply = Reply(ReplyType.TEXT, start_msg)
                    start_reply = self._decorate_reply(context, start_reply)
                    self._send_reply(context, start_reply)

                    success_count = 0
                    fail_count = 0

                    # 循环生成图片
                    for i in range(gacha_count_final):
                        logger.info(f"[chat_channel] GACHA generating image {i+1}/{gacha_count_final}...")

                        try:
                            if ref_images:
                                # 图生图模式
                                ok, result = kgapi.edit_img(final_content, ref_images)
                            else:
                                # 文生图模式
                                ok, result = kgapi.create_img(final_content)

                            if ok:
                                # 发送图片
                                img_reply = Reply(ReplyType.IMAGE_URL, result)
                                img_reply = self._decorate_reply(context, img_reply)
                                self._send_reply(context, img_reply)

                                # 发送进度提示
                                progress_msg = f"第{i+1}/{gacha_count_final}张生成完成"
                                progress_reply = Reply(ReplyType.TEXT, progress_msg)
                                progress_reply = self._decorate_reply(context, progress_reply)
                                self._send_reply(context, progress_reply)

                                success_count += 1
                                logger.info(f"[chat_channel] GACHA image {i+1}/{gacha_count_final} success")
                            else:
                                # 生成失败
                                fail_msg = f"第{i+1}/{gacha_count_final}张生成失败: {result}"
                                fail_reply = Reply(ReplyType.TEXT, fail_msg)
                                fail_reply = self._decorate_reply(context, fail_reply)
                                self._send_reply(context, fail_reply)

                                fail_count += 1
                                logger.warning(f"[chat_channel] GACHA image {i+1}/{gacha_count_final} failed: {result}")
                        except Exception as e:
                            fail_msg = f"第{i+1}/{gacha_count_final}张生成异常: {str(e)}"
                            fail_reply = Reply(ReplyType.TEXT, fail_msg)
                            fail_reply = self._decorate_reply(context, fail_reply)
                            self._send_reply(context, fail_reply)

                            fail_count += 1
                            logger.error(f"[chat_channel] GACHA image {i+1}/{gacha_count_final} exception: {e}")

                    # 清理待处理队列
                    if session_id in pending_image_create:
                        del pending_image_create[session_id]

                    # 发送完成提示
                    if fail_count == 0:
                        end_msg = f"抽卡完成！共生成{success_count}张图片"
                    else:
                        end_msg = f"抽卡完成！成功{success_count}张，失败{fail_count}张"
                    end_reply = Reply(ReplyType.TEXT, end_msg)
                    end_reply = self._decorate_reply(context, end_reply)
                    self._send_reply(context, end_reply)

                    logger.info(f"[chat_channel] GACHA completed: success={success_count}, fail={fail_count}")

                # 检查是否有最近的图片
                recent_images = []
                if session_id in memory.USER_IMAGE_CACHE:
                    cached = memory.USER_IMAGE_CACHE[session_id]
                    if time.time() - cached.get("time", 0) < TEXT_WAIT_TIME:
                        img_path = cached.get("path")
                        if img_path:
                            recent_images.append(img_path)
                            logger.info(f"[chat_channel] GACHA found recent image from cache: {img_path}")

                # 保存待处理的抽卡消息（复用 pending_image_create）
                pending_image_create[session_id] = {
                    "context": context,
                    "time": time.time(),
                    "result": None,
                    "cancelled": False,
                    "ref_images": recent_images
                }

                context["channel"] = e_context["channel"]

                # 提交异步处理任务
                future = handler_pool.submit(gacha_image_create_handler)
                pending_image_create[session_id]["future"] = future

                # 不返回 reply，由异步任务处理
                return Reply()
            elif context.type == ContextType.VOICE:  # 语音消息
                cmsg = context["msg"]
                cmsg.prepare()
                file_path = context.content
                wav_path = os.path.splitext(file_path)[0] + ".wav"
                try:
                    any_to_wav(file_path, wav_path)
                except Exception as e:  # 转换失败，直接使用mp3，对于某些api，mp3也可以识别
                    logger.warning("[chat_channel]any to wav error, use raw path. " + str(e))
                    wav_path = file_path
                # 语音识别
                reply = super().build_voice_to_text(wav_path)
                # 删除临时文件
                try:
                    os.remove(file_path)
                    if wav_path != file_path:
                        os.remove(wav_path)
                except Exception as e:
                    pass
                    # logger.warning("[chat_channel]delete temp file error: " + str(e))

                if reply.type == ReplyType.TEXT:
                    new_context = self._compose_context(ContextType.TEXT, reply.content, **context.kwargs)
                    if new_context:
                        reply = self._generate_reply(new_context)
                    else:
                        return
            elif context.type == ContextType.IMAGE:  # 图片消息，进行图片识别
                session_id = context.get("session_id")

                # 检查是否有待处理的文本消息（用于图片+文本组合处理）
                if session_id and session_id in pending_text_messages:
                    # 有待处理的文本，将图片添加到列表
                    pending = pending_text_messages[session_id]

                    # 确保图片已下载
                    cmsg = context["msg"]
                    cmsg.prepare()

                    # 添加图片到列表
                    pending["images"].append(context.content)

                    # 更新最后收到图片的时间，重置倒计时
                    pending["last_image_time"] = time.time()
                    logger.info(f"[chat_channel] image added to TEXT task (total: {len(pending['images'])}), countdown reset to {IMAGE_WAIT_TIME}s")

                    # 不在这里处理，让异步任务处理
                    return Reply()

                # 检查是否有待处理的生图请求（用于图生图）
                if session_id and session_id in pending_image_create:
                    # 有待处理的生图请求，将此图片作为参考图片
                    pending = pending_image_create[session_id]
                    img_create_context = pending["context"]

                    logger.info(f"[chat_channel] found pending image create, adding reference image for: {img_create_context.content[:50]}...")

                    # 确保图片已下载
                    cmsg = context["msg"]
                    cmsg.prepare()

                    # 添加参考图片路径
                    pending["ref_images"].append(context.content)

                    # 更新最后收到图片的时间，重置倒计时
                    pending["last_image_time"] = time.time()
                    logger.info(f"[chat_channel] reference image added (total: {len(pending['ref_images'])}), countdown reset to {IMAGE_WAIT_TIME}s")

                    # 不在这里发送提示消息，等倒计时结束后统一发送
                    # 这样可以准确显示收到的图片数量
                    return Reply()

                # 保存图片到缓存（用于某些插件，以及"先图片后生图文本"的场景）
                cmsg = context["msg"]
                cmsg.prepare()  # 确保图片已下载

                memory.USER_IMAGE_CACHE[session_id] = {
                    "path": context.content,
                    "msg": cmsg,
                    "time": time.time()
                }

                # 等待10秒，看是否有"生图"或普通文本到来
                # 如果有，这张图片会被添加到对应的pending队列，不需要单独进行图片识别
                # 注：手机端图片和文字是分开的输入框，需要足够的等待时间
                logger.info("[chat_channel] image received, waiting 10s to check if TEXT or IMAGE_CREATE will come...")
                time.sleep(10)

                # 检查是否已经有pending_image_create（用户发送了"生图"文本）
                if session_id in pending_image_create:
                    # 检查这张图片是否已经被添加到ref_images
                    pending = pending_image_create[session_id]
                    if context.content in pending.get("ref_images", []):
                        logger.info("[chat_channel] image already added to IMAGE_CREATE, skip image recognition")
                        return Reply()
                    else:
                        # 图片还没被添加，手动添加
                        pending["ref_images"].append(context.content)
                        logger.info("[chat_channel] image added to IMAGE_CREATE after waiting, skip image recognition")
                        return Reply()

                # 再次检查是否有pending_text_messages（用户在等待期间发送了文本）
                if session_id in pending_text_messages:
                    pending = pending_text_messages[session_id]
                    # 添加图片到列表
                    pending["images"].append(context.content)
                    # 更新最后收到图片的时间，重置倒计时
                    pending["last_image_time"] = time.time()
                    logger.info(f"[chat_channel] image added to TEXT task after waiting (total: {len(pending['images'])}), countdown reset to {IMAGE_WAIT_TIME}s")
                    return Reply()

                # 没有pending_image_create也没有pending_text_messages，进行正常的图片识别
                logger.info("[chat_channel] no pending task found, processing image message: {}".format(context.content))

                context["channel"] = e_context["channel"]
                reply = super().build_reply_content(context.content, context)
            elif context.type == ContextType.VIDEO:  # 视频消息，进行视频识别
                session_id = context.get("session_id")

                # 检查是否有待处理的文本消息
                text_query = None
                if session_id and session_id in pending_text_messages:
                    # 有待处理的文本，合并处理
                    pending = pending_text_messages[session_id]
                    text_context = pending["context"]
                    text_query = text_context.content

                    logger.info(f"[chat_channel] found pending text message, merging with video: {text_query[:50]}...")

                    # 取消延迟任务
                    if "future" in pending and not pending["future"].done():
                        pending["future"].cancel()

                    # 从待处理队列中移除
                    del pending_text_messages[session_id]

                # 调用 bot 进行视频识别
                logger.info("[chat_channel] processing video message: {}".format(context.content))
                cmsg = context["msg"]
                cmsg.prepare()  # 确保视频已下载

                # 如果有文本，将其传递给 bot
                if text_query:
                    context["video_query"] = text_query

                context["channel"] = e_context["channel"]
                reply = super().build_reply_content(context.content, context)
            elif context.type == ContextType.SHARING:  # 分享信息，当前无默认逻辑
                pass
            elif context.type == ContextType.FUNCTION:  # 函数调用等，当前无默认逻辑
                pass
            else:
                logger.warning("[chat_channel] unknown context type: {}".format(context.type))
                return
        return reply

    def _decorate_reply(self, context: Context, reply: Reply) -> Reply:
        if reply and reply.type:
            e_context = PluginManager().emit_event(
                EventContext(
                    Event.ON_DECORATE_REPLY,
                    {"channel": self, "context": context, "reply": reply},
                )
            )
            reply = e_context["reply"]
            desire_rtype = context.get("desire_rtype")
            if not e_context.is_pass() and reply and reply.type:
                if reply.type in self.NOT_SUPPORT_REPLYTYPE:
                    logger.error("[chat_channel]reply type not support: " + str(reply.type))
                    reply.type = ReplyType.ERROR
                    reply.content = "不支持发送的消息类型: " + str(reply.type)

                if reply.type == ReplyType.TEXT:
                    reply_text = reply.content
                    if desire_rtype == ReplyType.VOICE and ReplyType.VOICE not in self.NOT_SUPPORT_REPLYTYPE:
                        reply = super().build_text_to_voice(reply.content)
                        return self._decorate_reply(context, reply)
                    if context.get("isgroup", False):
                        if not context.get("no_need_at", False):
                            reply_text = "@" + context["msg"].actual_user_nickname + "\n" + reply_text.strip()
                        reply_text = conf().get("group_chat_reply_prefix", "") + reply_text + conf().get("group_chat_reply_suffix", "")
                    else:
                        reply_text = conf().get("single_chat_reply_prefix", "") + reply_text + conf().get("single_chat_reply_suffix", "")
                    reply.content = reply_text
                elif reply.type == ReplyType.ERROR or reply.type == ReplyType.INFO:
                    reply.content = "[" + str(reply.type) + "]\n" + reply.content
                elif reply.type == ReplyType.IMAGE_URL or reply.type == ReplyType.VOICE or reply.type == ReplyType.IMAGE or reply.type == ReplyType.FILE or reply.type == ReplyType.VIDEO or reply.type == ReplyType.VIDEO_URL:
                    pass
                else:
                    logger.error("[chat_channel] unknown reply type: {}".format(reply.type))
                    return
            if desire_rtype and desire_rtype != reply.type and reply.type not in [ReplyType.ERROR, ReplyType.INFO]:
                logger.warning("[chat_channel] desire_rtype: {}, but reply type: {}".format(context.get("desire_rtype"), reply.type))
            return reply

    def _send_reply(self, context: Context, reply: Reply):
        if reply and reply.type:
            e_context = PluginManager().emit_event(
                EventContext(
                    Event.ON_SEND_REPLY,
                    {"channel": self, "context": context, "reply": reply},
                )
            )
            reply = e_context["reply"]
            if not e_context.is_pass() and reply and reply.type:
                logger.debug("[chat_channel] ready to send reply: {}, context: {}".format(reply, context))
                self._send(reply, context)

    def _send(self, reply: Reply, context: Context, retry_cnt=0):
        try:
            self.send(reply, context)
        except Exception as e:
            logger.error("[chat_channel] sendMsg error: {}".format(str(e)))
            if isinstance(e, NotImplementedError):
                return
            logger.exception(e)
            if retry_cnt < 2:
                time.sleep(3 + 3 * retry_cnt)
                self._send(reply, context, retry_cnt + 1)
            else:
                # 重试失败后发送错误通知
                notify_channel_error("ChatChannel", f"消息发送失败: {str(e)}", exception=e)

    def _success_callback(self, session_id, **kwargs):  # 线程正常结束时的回调函数
        logger.debug("Worker return success, session_id = {}".format(session_id))

    def _fail_callback(self, session_id, exception, **kwargs):  # 线程异常结束时的回调函数
        logger.exception("Worker return exception: {}".format(exception))
        # 发送错误通知
        notify_channel_error("ChatChannel", f"消息处理线程异常: {str(exception)}", exception=exception)

    def _thread_pool_callback(self, session_id, **kwargs):
        def func(worker: Future):
            try:
                worker_exception = worker.exception()
                if worker_exception:
                    self._fail_callback(session_id, exception=worker_exception, **kwargs)
                else:
                    self._success_callback(session_id, **kwargs)
            except CancelledError as e:
                logger.info("Worker cancelled, session_id = {}".format(session_id))
            except Exception as e:
                logger.exception("Worker raise exception: {}".format(e))
            with self.lock:
                self.sessions[session_id][1].release()

        return func

    def produce(self, context: Context):
        session_id = context["session_id"]
        with self.lock:
            if session_id not in self.sessions:
                self.sessions[session_id] = [
                    Dequeue(),
                    threading.BoundedSemaphore(conf().get("concurrency_in_session", 4)),
                ]
            if context.type == ContextType.TEXT and context.content.startswith("#"):
                self.sessions[session_id][0].putleft(context)  # 优先处理管理命令
            else:
                self.sessions[session_id][0].put(context)

    # 消费者函数，单独线程，用于从消息队列中取出消息并处理
    def consume(self):
        while True:
            with self.lock:
                session_ids = list(self.sessions.keys())
            for session_id in session_ids:
                with self.lock:
                    context_queue, semaphore = self.sessions[session_id]
                if semaphore.acquire(blocking=False):  # 等线程处理完毕才能删除
                    if not context_queue.empty():
                        context = context_queue.get()
                        logger.debug("[chat_channel] consume context: {}".format(context))
                        future: Future = handler_pool.submit(self._handle, context)
                        future.add_done_callback(self._thread_pool_callback(session_id, context=context))
                        with self.lock:
                            if session_id not in self.futures:
                                self.futures[session_id] = []
                            self.futures[session_id].append(future)
                    elif semaphore._initial_value == semaphore._value + 1:  # 除了当前，没有任务再申请到信号量，说明所有任务都处理完毕
                        with self.lock:
                            self.futures[session_id] = [t for t in self.futures[session_id] if not t.done()]
                            assert len(self.futures[session_id]) == 0, "thread pool error"
                            del self.sessions[session_id]
                    else:
                        semaphore.release()
            time.sleep(0.2)

    # 取消session_id对应的所有任务，只能取消排队的消息和已提交线程池但未执行的任务
    def cancel_session(self, session_id):
        with self.lock:
            if session_id in self.sessions:
                for future in self.futures[session_id]:
                    future.cancel()
                cnt = self.sessions[session_id][0].qsize()
                if cnt > 0:
                    logger.info("Cancel {} messages in session {}".format(cnt, session_id))
                self.sessions[session_id][0] = Dequeue()

    def cancel_all_session(self):
        with self.lock:
            for session_id in self.sessions:
                for future in self.futures[session_id]:
                    future.cancel()
                cnt = self.sessions[session_id][0].qsize()
                if cnt > 0:
                    logger.info("Cancel {} messages in session {}".format(cnt, session_id))
                self.sessions[session_id][0] = Dequeue()


def check_prefix(content, prefix_list):
    if not prefix_list:
        return None
    for prefix in prefix_list:
        if content.startswith(prefix):
            return prefix
    return None


def check_contain(content, keyword_list):
    if not keyword_list:
        return None
    for ky in keyword_list:
        if content.find(ky) != -1:
            return True
    return None

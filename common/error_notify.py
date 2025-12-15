# encoding:utf-8

"""
企业微信Webhook错误通知模块
用于在发生错误时通过企业微信群机器人推送告警消息
"""

import json
import requests
import threading
import time
import traceback
from datetime import datetime
from common.log import logger

# 错误通知配置
_notify_config = {
    "enabled": False,
    "webhook_url": "",
    "mentioned_mobile_list": [],  # 需要@的手机号列表
    "rate_limit_seconds": 60,  # 同类错误的最小通知间隔（秒）
}

# 用于限流的错误记录
_error_cache = {}
_cache_lock = threading.Lock()


def init_error_notify(webhook_url: str, mentioned_mobiles: list = None, rate_limit: int = 60):
    """
    初始化错误通知配置

    :param webhook_url: 企业微信webhook地址
    :param mentioned_mobiles: 需要@的手机号列表
    :param rate_limit: 同类错误的最小通知间隔（秒）
    """
    global _notify_config
    _notify_config["enabled"] = bool(webhook_url)
    _notify_config["webhook_url"] = webhook_url
    _notify_config["mentioned_mobile_list"] = mentioned_mobiles or []
    _notify_config["rate_limit_seconds"] = rate_limit
    if _notify_config["enabled"]:
        logger.info("[ErrorNotify] 错误通知已启用，webhook: {}...".format(webhook_url[:50]))


def _should_notify(error_key: str) -> bool:
    """
    检查是否应该发送通知（限流检查）

    :param error_key: 错误标识
    :return: 是否应该发送
    """
    current_time = time.time()
    with _cache_lock:
        last_time = _error_cache.get(error_key, 0)
        if current_time - last_time >= _notify_config["rate_limit_seconds"]:
            _error_cache[error_key] = current_time
            return True
        return False


def _send_webhook(content: str) -> bool:
    """
    发送企业微信webhook消息

    :param content: markdown格式的消息内容
    :return: 是否发送成功
    """
    if not _notify_config["enabled"] or not _notify_config["webhook_url"]:
        return False

    try:
        # 构建@用户的文本
        mention_text = ""
        if _notify_config["mentioned_mobile_list"]:
            for mobile in _notify_config["mentioned_mobile_list"]:
                mention_text += f"<@{mobile}>"

        # 构建消息体
        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": content + "\n" + mention_text if mention_text else content
            }
        }

        response = requests.post(
            _notify_config["webhook_url"],
            json=data,
            headers={"Content-Type": "application/json"},
            timeout=10
        )

        result = response.json()
        if result.get("errcode") == 0:
            logger.debug("[ErrorNotify] 错误通知发送成功")
            return True
        else:
            logger.warning("[ErrorNotify] 错误通知发送失败: {}".format(result))
            return False

    except Exception as e:
        logger.warning("[ErrorNotify] 发送webhook异常: {}".format(e))
        return False


def notify_error(
    error_type: str,
    error_msg: str,
    module: str = "",
    detail: str = "",
    exception: Exception = None
):
    """
    发送错误通知

    :param error_type: 错误类型（如：模型请求错误、通道错误等）
    :param error_msg: 错误消息
    :param module: 发生错误的模块
    :param detail: 详细信息
    :param exception: 异常对象
    """
    if not _notify_config["enabled"]:
        return

    # 生成错误标识用于限流
    error_key = f"{error_type}:{module}:{error_msg[:50]}"

    if not _should_notify(error_key):
        logger.debug("[ErrorNotify] 错误通知被限流: {}".format(error_key))
        return

    # 构建markdown消息
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 获取异常堆栈
    stack_trace = ""
    if exception:
        stack_trace = traceback.format_exception(type(exception), exception, exception.__traceback__)
        stack_trace = "".join(stack_trace[-3:])  # 只取最后3行
        if len(stack_trace) > 500:
            stack_trace = stack_trace[:500] + "..."

    content = f"""## <font color="warning">系统错误告警</font>

**错误类型：**<font color="warning">{error_type}</font>
**发生模块：**<font color="comment">{module or '未知'}</font>
**发生时间：**<font color="comment">{now}</font>

**错误信息：**
>{error_msg[:500]}"""

    if detail:
        content += f"""

**详细信息：**
>{detail[:300]}"""

    if stack_trace:
        content += f"""

**堆栈跟踪：**
`{stack_trace[:200]}`"""

    # 异步发送通知
    threading.Thread(target=_send_webhook, args=(content,), daemon=True).start()


def notify_model_error(model_name: str, error_msg: str, exception: Exception = None):
    """
    发送模型请求错误通知

    :param model_name: 模型名称
    :param error_msg: 错误消息
    :param exception: 异常对象
    """
    notify_error(
        error_type="模型请求错误",
        error_msg=error_msg,
        module=f"Bot/{model_name}",
        exception=exception
    )


def notify_channel_error(channel_name: str, error_msg: str, exception: Exception = None):
    """
    发送通道错误通知

    :param channel_name: 通道名称
    :param error_msg: 错误消息
    :param exception: 异常对象
    """
    notify_error(
        error_type="通道错误",
        error_msg=error_msg,
        module=f"Channel/{channel_name}",
        exception=exception
    )


def notify_plugin_error(plugin_name: str, error_msg: str, exception: Exception = None):
    """
    发送插件错误通知

    :param plugin_name: 插件名称
    :param error_msg: 错误消息
    :param exception: 异常对象
    """
    notify_error(
        error_type="插件错误",
        error_msg=error_msg,
        module=f"Plugin/{plugin_name}",
        exception=exception
    )


def notify_system_error(error_msg: str, module: str = "", exception: Exception = None):
    """
    发送系统错误通知

    :param error_msg: 错误消息
    :param module: 模块名称
    :param exception: 异常对象
    """
    notify_error(
        error_type="系统错误",
        error_msg=error_msg,
        module=module,
        exception=exception
    )

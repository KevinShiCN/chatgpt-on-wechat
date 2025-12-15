# encoding:utf-8

import os
import signal
import sys
import time

from channel import channel_factory
from common import const
from common.error_notify import init_error_notify
from config import load_config
from plugins import *
import threading


def sigterm_handler_wrap(_signo):
    old_handler = signal.getsignal(_signo)

    def func(_signo, _stack_frame):
        logger.info("signal {} received, exiting...".format(_signo))
        conf().save_user_datas()
        if callable(old_handler):  #  check old_handler
            return old_handler(_signo, _stack_frame)
        sys.exit(0)

    signal.signal(_signo, func)


def start_channel(channel_name: str):
    channel = channel_factory.create_channel(channel_name)
    if channel_name in ["wx", "wxy", "terminal", "wechatmp", "web", "wechatmp_service", "wechatcom_app", "wework",
                        const.FEISHU, const.DINGTALK]:
        PluginManager().load_plugins()

    if conf().get("use_linkai"):
        try:
            from common import linkai_client
            threading.Thread(target=linkai_client.start, args=(channel,)).start()
        except Exception as e:
            pass
    channel.startup()


def run():
    try:
        # load config
        load_config()

        # 初始化错误通知
        if conf().get("error_notify_enabled", False):
            init_error_notify(
                webhook_url=conf().get("error_notify_webhook", ""),
                mentioned_mobiles=conf().get("error_notify_mentioned_mobiles", []),
                rate_limit=conf().get("error_notify_rate_limit", 60)
            )

        # ctrl + c
        sigterm_handler_wrap(signal.SIGINT)
        # kill signal
        sigterm_handler_wrap(signal.SIGTERM)

        # create channel
        channel_name = conf().get("channel_type", "wx")

        if "--cmd" in sys.argv:
            channel_name = "terminal"

        if channel_name == "wxy":
            os.environ["WECHATY_LOG"] = "warn"

        start_channel(channel_name)

        while True:
            time.sleep(1)
    except Exception as e:
        logger.error("App startup failed!")
        logger.exception(e)


if __name__ == "__main__":
    run()

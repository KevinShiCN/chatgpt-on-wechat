# encoding:utf-8
"""
KGAPI 图像生成模块
支持文生图和图生图功能
复用 open_ai_api_base 配置
"""

import requests
import time
import os
import socket
from common.log import logger
from config import conf

# 设置socket默认超时时间（解决写入超时问题）
socket.setdefaulttimeout(180)


class KGAPIImage:
    def __init__(self):
        self.api_key = conf().get("open_ai_api_key")
        api_base = conf().get("open_ai_api_base", "https://api.openai.com/v1")
        self.api_base = api_base.rstrip("/")
        if not self.api_base.endswith("/v1"):
            self.api_base = self.api_base + "/v1"
        
        self.model = conf().get("kgapi_image_model", "nano-banana-2-4k")
        self.image_size = conf().get("kgapi_image_size", "4K")

    def create_img(self, query, retry_count=0, api_key=None):
        """文生图"""
        try:
            if not self.api_key and not api_key:
                return False, "API Key未配置"

            logger.info(f"[KGAPI] create_img query={query}")
            
            url = f"{self.api_base}/images/generations"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key or self.api_key}"
            }
            data = {
                "model": self.model,
                "prompt": query,
                "response_format": "url"
            }
            
            if "nano-banana-2" in self.model:
                data["image_size"] = self.image_size
            
            res = requests.post(url, headers=headers, json=data, timeout=(5, 120))
            res.raise_for_status()
            
            result = res.json()
            image_url = result["data"][0]["url"]
            logger.info(f"[KGAPI] create_img success, url={image_url}")
            return True, image_url

        except requests.exceptions.Timeout:
            if retry_count < 2:
                time.sleep(2)
                return self.create_img(query, retry_count + 1, api_key)
            return False, "生图超时"
        except Exception as e:
            logger.error(f"[KGAPI] create_img error: {e}")
            return False, f"生图失败: {str(e)}"

    def edit_img(self, query, image_paths, retry_count=0, api_key=None):
        """图生图"""
        try:
            if not self.api_key and not api_key:
                return False, "API Key未配置"

            if not image_paths:
                return False, "未提供参考图片"

            logger.info(f"[KGAPI] edit_img start: query={query}, images={len(image_paths)}, retry={retry_count}")

            url = f"{self.api_base}/images/edits"
            headers = {
                "Authorization": f"Bearer {api_key or self.api_key}"
            }

            files = []
            opened_files = []
            total_size = 0
            for img_path in image_paths:
                if os.path.exists(img_path):
                    file_size = os.path.getsize(img_path)
                    total_size += file_size
                    logger.info(f"[KGAPI] loading image: {img_path}, size={file_size/1024:.1f}KB")
                    f = open(img_path, 'rb')
                    opened_files.append(f)
                    files.append(("image", (os.path.basename(img_path), f, 'image/png')))
                else:
                    logger.warning(f"[KGAPI] image not found: {img_path}")

            if not files:
                return False, "参考图片文件不存在"

            logger.info(f"[KGAPI] total upload size: {total_size/1024:.1f}KB")

            data = {
                "model": self.model,
                "prompt": query,
                "response_format": "url"
            }

            if "nano-banana-2" in self.model:
                data["image_size"] = self.image_size

            logger.info(f"[KGAPI] sending request to {url}, model={self.model}, image_size={self.image_size}")
            start_time = time.time()

            # 增加超时时间：连接30秒，读取180秒（上传图片需要更长时间）
            res = requests.post(url, headers=headers, files=files, data=data, timeout=(30, 180))

            elapsed = time.time() - start_time
            logger.info(f"[KGAPI] request completed in {elapsed:.1f}s, status={res.status_code}")

            for f in opened_files:
                f.close()

            res.raise_for_status()

            result = res.json()
            image_url = result["data"][0]["url"]
            logger.info(f"[KGAPI] edit_img success, url={image_url}")
            return True, image_url

        except requests.exceptions.Timeout as e:
            elapsed = time.time() - start_time if 'start_time' in locals() else 0
            logger.error(f"[KGAPI] edit_img timeout after {elapsed:.1f}s: {e}")
            if retry_count < 2:
                logger.info(f"[KGAPI] retrying edit_img ({retry_count + 1}/2)...")
                time.sleep(2)
                return self.edit_img(query, image_paths, retry_count + 1, api_key)
            return False, f"图生图超时（已重试{retry_count}次）"
        except requests.exceptions.RequestException as e:
            logger.error(f"[KGAPI] edit_img request error: {type(e).__name__}: {e}")
            return False, f"图生图请求失败: {str(e)}"
        except Exception as e:
            logger.error(f"[KGAPI] edit_img error: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[KGAPI] traceback: {traceback.format_exc()}")
            return False, f"图生图失败: {str(e)}"

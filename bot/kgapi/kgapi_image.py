# encoding:utf-8
"""
KGAPI 图像生成模块
支持文生图和图生图功能
复用 open_ai_api_base 配置
"""

import requests
import time
import os
from common.log import logger
from config import conf


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

            logger.info(f"[KGAPI] edit_img query={query}, images={len(image_paths)}")
            
            url = f"{self.api_base}/images/edits"
            headers = {
                "Authorization": f"Bearer {api_key or self.api_key}"
            }
            
            files = []
            opened_files = []
            for img_path in image_paths:
                if os.path.exists(img_path):
                    f = open(img_path, 'rb')
                    opened_files.append(f)
                    files.append(("image", (os.path.basename(img_path), f, 'image/png')))
            
            if not files:
                return False, "参考图片文件不存在"
            
            data = {
                "model": self.model,
                "prompt": query,
                "response_format": "url"
            }
            
            if "nano-banana-2" in self.model:
                data["image_size"] = self.image_size
            
            res = requests.post(url, headers=headers, files=files, data=data, timeout=(5, 120))
            
            for f in opened_files:
                f.close()
            
            res.raise_for_status()
            
            result = res.json()
            image_url = result["data"][0]["url"]
            logger.info(f"[KGAPI] edit_img success, url={image_url}")
            return True, image_url

        except requests.exceptions.Timeout:
            if retry_count < 2:
                time.sleep(2)
                return self.edit_img(query, image_paths, retry_count + 1, api_key)
            return False, "图生图超时"
        except Exception as e:
            logger.error(f"[KGAPI] edit_img error: {e}")
            return False, f"图生图失败: {str(e)}"

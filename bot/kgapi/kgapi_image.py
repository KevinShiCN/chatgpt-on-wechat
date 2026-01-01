# encoding:utf-8
"""
KGAPI 图像生成模块
支持文生图和图生图功能
复用 open_ai_api_base 配置
"""

import requests
import time
import os
import re
import socket
import threading
from common.log import logger
from config import conf
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor

# 设置socket默认超时时间（解决写入超时问题）
socket.setdefaulttimeout(180)

# 上传信号量：限制同时只有一个上传任务，避免并发上传导致超时
_upload_semaphore = threading.Semaphore(1)

# 支持的 aspect_ratio 值
SUPPORTED_ASPECT_RATIOS = {
    "1:1", "4:3", "3:4", "16:9", "9:16",
    "2:3", "3:2", "4:5", "5:4", "21:9"
}


class KGAPIImage:
    def __init__(self):
        self.api_key = conf().get("open_ai_api_key")
        api_base = conf().get("open_ai_api_base", "https://api.openai.com/v1")
        self.api_base = api_base.rstrip("/")
        if not self.api_base.endswith("/v1"):
            self.api_base = self.api_base + "/v1"

        self.model = conf().get("kgapi_image_model", "nano-banana-2-4k")
        self.image_size = conf().get("kgapi_image_size", "4K")

    @staticmethod
    def parse_aspect_ratio(query):
        """
        从提示词中解析 aspect_ratio
        支持格式: 16:9, 16：9 (中英文冒号兼容)
        返回: (aspect_ratio, cleaned_query)
        """
        # 匹配比例格式，支持中英文冒号
        pattern = r'\b(\d{1,2})\s*[：:]\s*(\d{1,2})\b'
        match = re.search(pattern, query)

        if match:
            w, h = match.groups()
            ratio = f"{w}:{h}"
            if ratio in SUPPORTED_ASPECT_RATIOS:
                # 从 query 中移除比例信息
                cleaned_query = re.sub(pattern, '', query, count=1).strip()
                cleaned_query = re.sub(r'\s+', ' ', cleaned_query)  # 清理多余空格
                logger.info(f"[KGAPI] parsed aspect_ratio: {ratio}")
                return ratio, cleaned_query

        return None, query

    def create_img(self, query, retry_count=0, api_key=None):
        """文生图"""
        try:
            if not self.api_key and not api_key:
                return False, "API Key未配置"

            # 解析 aspect_ratio
            aspect_ratio, cleaned_query = self.parse_aspect_ratio(query)

            # 强制添加生图指令，避免 API 返回文字分析而非图片
            if "直接生成图片" not in cleaned_query:
                cleaned_query = cleaned_query.rstrip() + "\n\n直接生成图片，不要返回文字分析。"

            logger.info(f"[KGAPI] create_img query={cleaned_query}, aspect_ratio={aspect_ratio}")

            url = f"{self.api_base}/images/generations"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key or self.api_key}"
            }
            data = {
                "model": self.model,
                "prompt": cleaned_query,
                "response_format": "url"
            }

            if "nano-banana-2" in self.model:
                data["image_size"] = self.image_size
                if aspect_ratio:
                    data["aspect_ratio"] = aspect_ratio

            # 连接超时10秒，读取超时600秒（10分钟），因为复杂图片生成可能需要较长时间
            res = requests.post(url, headers=headers, json=data, timeout=(10, 600))
            res.raise_for_status()

            result = res.json()
            logger.debug(f"[KGAPI] create_img API response: {result}")

            # 检查返回数据结构
            if "data" not in result or not result["data"]:
                error_msg = result.get("error", {}).get("message") if isinstance(result.get("error"), dict) else str(result.get("error", result))
                logger.error(f"[KGAPI] create_img API返回异常，完整响应: {result}")
                return False, f"生图失败: API返回异常({error_msg})，请联系管理员干饭CEO"

            image_url = result["data"][0].get("url")
            if not image_url:
                # 检查是否有 revised_prompt（API 可能返回文字分析而非图片）
                revised_prompt = result["data"][0].get("revised_prompt")
                if revised_prompt:
                    logger.warning(f"[KGAPI] create_img API返回文字分析而非图片，长度: {len(revised_prompt)}")
                    # 返回特殊标记，让调用方知道这是文字内容
                    return "text", revised_prompt
                logger.error(f"[KGAPI] create_img API未返回url字段，data[0]: {result['data'][0]}")
                return False, "生图失败: API未返回图片URL，请联系管理员干饭CEO"

            logger.info(f"[KGAPI] create_img success, url={image_url}")
            return True, image_url

        except requests.exceptions.Timeout:
            logger.error(f"[KGAPI] create_img timeout, retry_count={retry_count}")
            # 单次已等待10分钟，最多重试1次
            if retry_count < 1:
                time.sleep(2)
                return self.create_img(query, retry_count + 1, api_key)
            return False, "生图超时（已等待超过10分钟），请联系管理员干饭CEO"
        except requests.exceptions.RequestException as e:
            logger.error(f"[KGAPI] create_img request error: {type(e).__name__}: {e}")
            return False, f"生图请求失败: {str(e)}，请联系管理员干饭CEO"
        except Exception as e:
            logger.error(f"[KGAPI] create_img error: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[KGAPI] create_img traceback: {traceback.format_exc()}")
            return False, f"生图失败: {str(e)}，请联系管理员干饭CEO"

    def edit_img(self, query, image_paths, retry_count=0, api_key=None):
        """图生图"""
        try:
            if not self.api_key and not api_key:
                return False, "API Key未配置"

            if not image_paths:
                return False, "未提供参考图片"

            # 解析 aspect_ratio
            aspect_ratio, cleaned_query = self.parse_aspect_ratio(query)
            logger.info(f"[KGAPI] edit_img start: query={cleaned_query}, aspect_ratio={aspect_ratio}, images={len(image_paths)}, retry={retry_count}")

            url = f"{self.api_base}/images/edits"
            headers = {
                "Authorization": f"Bearer {api_key or self.api_key}"
            }

            # 检查图片文件
            valid_image = None
            for img_path in image_paths:
                if os.path.exists(img_path):
                    file_size = os.path.getsize(img_path)
                    logger.info(f"[KGAPI] loading image: {img_path}, size={file_size/1024:.1f}KB")
                    valid_image = img_path
                    break
                else:
                    logger.warning(f"[KGAPI] image not found: {img_path}")

            if not valid_image:
                return False, "参考图片文件不存在"

            data = {
                "model": self.model,
                "prompt": cleaned_query,
                "response_format": "url"
            }

            if "nano-banana-2" in self.model:
                data["image_size"] = self.image_size
                if aspect_ratio:
                    data["aspect_ratio"] = aspect_ratio

            logger.info(f"[KGAPI] sending request to {url}, model={self.model}, image_size={self.image_size}, aspect_ratio={aspect_ratio}")
            start_time = time.time()

            # 使用 MultipartEncoder 构建请求体
            fields = dict(data)  # 复制 data 字典
            fields['image'] = (os.path.basename(valid_image), open(valid_image, 'rb'), 'image/png')

            encoder = MultipartEncoder(fields=fields)

            # 上传完成标志
            upload_completed = [False]

            def upload_callback(monitor):
                """上传进度回调，上传完成后释放信号量"""
                if not upload_completed[0] and monitor.bytes_read >= monitor.len:
                    upload_completed[0] = True
                    upload_time = time.time() - start_time
                    logger.info(f"[KGAPI] upload completed in {upload_time:.1f}s, waiting for server response...")
                    _upload_semaphore.release()  # 释放信号量，允许下一个上传

            monitor = MultipartEncoderMonitor(encoder, upload_callback)

            # 获取上传信号量（等待其他上传完成）
            logger.info(f"[KGAPI] waiting for upload slot...")
            _upload_semaphore.acquire()
            logger.info(f"[KGAPI] got upload slot, starting upload...")

            try:
                # 发送请求，上传完成后回调会释放信号量
                res = requests.post(
                    url,
                    headers={**headers, 'Content-Type': monitor.content_type},
                    data=monitor,
                    timeout=(60, 300)  # 增加超时：上传60秒，等待响应300秒
                )
            except Exception as e:
                # 如果请求失败且信号量未释放，需要释放
                if not upload_completed[0]:
                    _upload_semaphore.release()
                raise

            elapsed = time.time() - start_time
            logger.info(f"[KGAPI] request completed in {elapsed:.1f}s, status={res.status_code}")

            res.raise_for_status()

            result = res.json()
            logger.debug(f"[KGAPI] edit_img API response: {result}")

            # 检查返回数据结构
            if "data" not in result or not result["data"]:
                error_msg = result.get("error", {}).get("message") if isinstance(result.get("error"), dict) else str(result.get("error", result))
                logger.error(f"[KGAPI] edit_img API返回异常，完整响应: {result}")
                return False, f"图生图失败: API返回异常({error_msg})，请联系管理员干饭CEO"

            image_url = result["data"][0].get("url")
            if not image_url:
                logger.error(f"[KGAPI] edit_img API未返回url字段，data[0]: {result['data'][0]}")
                return False, "图生图失败: API未返回图片URL，请联系管理员干饭CEO"

            logger.info(f"[KGAPI] edit_img success, url={image_url}")
            return True, image_url

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            elapsed = time.time() - start_time if 'start_time' in locals() else 0
            error_type = "超时" if isinstance(e, requests.exceptions.Timeout) else "连接失败"
            logger.error(f"[KGAPI] edit_img {error_type} after {elapsed:.1f}s: {e}")
            if retry_count < 2:
                wait_time = 5 * (retry_count + 1)  # 5秒、10秒递增
                logger.info(f"[KGAPI] retrying edit_img ({retry_count + 1}/2) after {wait_time}s...")
                time.sleep(wait_time)
                return self.edit_img(query, image_paths, retry_count + 1, api_key)
            return False, f"图生图{error_type}（已重试{retry_count}次），请联系管理员干饭CEO"
        except requests.exceptions.RequestException as e:
            logger.error(f"[KGAPI] edit_img request error: {type(e).__name__}: {e}")
            return False, f"图生图请求失败: {str(e)}，请联系管理员干饭CEO"
        except Exception as e:
            logger.error(f"[KGAPI] edit_img error: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[KGAPI] edit_img traceback: {traceback.format_exc()}")
            return False, f"图生图失败: {str(e)}，请联系管理员干饭CEO"

import io
import os
import re
from urllib.parse import urlparse
from PIL import Image
from common.log import logger

def fsize(file):
    if isinstance(file, io.BytesIO):
        return file.getbuffer().nbytes
    elif isinstance(file, str):
        return os.path.getsize(file)
    elif hasattr(file, "seek") and hasattr(file, "tell"):
        pos = file.tell()
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(pos)
        return size
    else:
        raise TypeError("Unsupported type")


def compress_imgfile(file, max_size):
    if fsize(file) <= max_size:
        return file
    file.seek(0)
    img = Image.open(file)
    rgb_image = img.convert("RGB")
    quality = 95
    while True:
        out_buf = io.BytesIO()
        rgb_image.save(out_buf, "JPEG", quality=quality)
        if fsize(out_buf) <= max_size:
            return out_buf
        quality -= 5


def split_string_by_utf8_length(string, max_length, max_split=0):
    encoded = string.encode("utf-8")
    start, end = 0, 0
    result = []
    while end < len(encoded):
        if max_split > 0 and len(result) >= max_split:
            result.append(encoded[start:].decode("utf-8"))
            break
        end = min(start + max_length, len(encoded))
        # 如果当前字节不是 UTF-8 编码的开始字节，则向前查找直到找到开始字节为止
        while end < len(encoded) and (encoded[end] & 0b11000000) == 0b10000000:
            end -= 1
        result.append(encoded[start:end].decode("utf-8"))
        start = end
    return result


def split_markdown_by_length(content, max_length=2048):
    """
    智能切分 Markdown 内容，避免在 HTML 标签或 Markdown 语法中间切断

    Args:
        content: 待切分的 Markdown 内容
        max_length: 最大字节长度，默认 2048

    Returns:
        切分后的字符串列表
    """
    if len(content.encode('utf-8')) <= max_length:
        return [content]

    result = []
    current_chunk = ""

    # 按行切分，保持内容完整性
    lines = content.split('\n')

    for i, line in enumerate(lines):
        line_with_newline = line + ('\n' if i < len(lines) - 1 else '')
        test_chunk = current_chunk + line_with_newline
        test_bytes = test_chunk.encode('utf-8')

        # 如果加上这行后超过限制
        if len(test_bytes) > max_length:
            # 如果当前块不为空，先保存
            if current_chunk:
                # 移除末尾的换行符，避免产生空行
                result.append(current_chunk.rstrip('\n'))
                current_chunk = line_with_newline
            else:
                # 单行就超过限制，需要强制切分
                # 但要避免在 HTML 标签中间切分
                parts = _split_long_line_safely(line, max_length)
                result.extend(parts[:-1])
                current_chunk = parts[-1] + ('\n' if i < len(lines) - 1 else '')
        else:
            current_chunk = test_chunk

    # 添加最后一块
    if current_chunk:
        result.append(current_chunk.rstrip('\n'))

    return result


def _split_long_line_safely(line, max_length):
    """
    安全地切分过长的单行，避免在 HTML 标签中间切断

    Args:
        line: 待切分的行
        max_length: 最大字节长度

    Returns:
        切分后的字符串列表
    """
    if len(line.encode('utf-8')) <= max_length:
        return [line]

    result = []
    current = ""
    i = 0

    while i < len(line):
        char = line[i]

        # 检测到 HTML 标签开始
        if char == '<':
            # 找到标签结束位置
            tag_end = line.find('>', i)
            if tag_end != -1:
                tag = line[i:tag_end + 1]
                test = current + tag

                # 如果加上整个标签后超过限制
                if len(test.encode('utf-8')) > max_length and current:
                    result.append(current)
                    current = tag
                else:
                    current = test
                i = tag_end + 1
                continue

        # 普通字符
        test = current + char
        if len(test.encode('utf-8')) > max_length and current:
            result.append(current)
            current = char
        else:
            current = test
        i += 1

    if current:
        result.append(current)

    return result if result else [line[:max_length]]



def get_path_suffix(path):
    path = urlparse(path).path
    return os.path.splitext(path)[-1].lstrip('.')


def convert_webp_to_png(webp_image):
    from PIL import Image
    try:
        webp_image.seek(0)
        img = Image.open(webp_image).convert("RGBA")
        png_image = io.BytesIO()
        img.save(png_image, format="PNG")
        png_image.seek(0)
        return png_image
    except Exception as e:
        logger.error(f"Failed to convert WEBP to PNG: {e}")
        raise


def remove_markdown_symbol(text: str):
    # 移除markdown格式，目前先移除**
    if not text:
        return text
    return re.sub(r'\*\*(.*?)\*\*', r'\1', text)

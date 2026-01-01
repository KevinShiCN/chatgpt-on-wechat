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


def split_markdown_by_length(content, max_length=2048, add_part_indicator=True):
    """
    智能切分 Markdown 内容，避免在 HTML 标签对中间切断

    核心策略：
    1. 追踪所有打开的标签（如 <font color="info">）
    2. 在切分点自动闭合所有打开的标签
    3. 在下一段开头重新打开这些标签
    4. 添加分段标识 [1/N] 让用户知道消息是连续的

    Args:
        content: 待切分的 Markdown 内容
        max_length: 最大字节长度，默认 2048
        add_part_indicator: 是否添加分段标识，默认 True

    Returns:
        切分后的字符串列表
    """
    if len(content.encode('utf-8')) <= max_length:
        return [content]

    # 预留分段标识的空间（如 "[99/99]\n" 最多 9 字节）
    indicator_reserve = 10 if add_part_indicator else 0
    effective_max_length = max_length - indicator_reserve

    result = []
    current_chunk = ""
    open_tags = []  # 追踪当前打开的标签，格式: [("font", 'color="info"'), ...]

    # 按行切分，保持内容完整性
    lines = content.split('\n')

    for i, line in enumerate(lines):
        line_with_newline = line + ('\n' if i < len(lines) - 1 else '')

        # 计算如果加上这行需要的总长度（包括可能需要的闭合标签）
        test_chunk = current_chunk + line_with_newline
        closing_tags = _generate_closing_tags(open_tags)
        test_with_closing = test_chunk + closing_tags
        test_bytes = len(test_with_closing.encode('utf-8'))

        # 如果加上这行后超过限制
        if test_bytes > effective_max_length:
            if current_chunk:
                # 闭合当前所有打开的标签，保存当前块
                closed_chunk = current_chunk.rstrip('\n') + closing_tags
                result.append(closed_chunk)

                # 重新打开标签，开始新块
                reopening_tags = _generate_opening_tags(open_tags)
                current_chunk = reopening_tags + line_with_newline
            else:
                # 单行就超过限制，需要强制切分
                parts = _split_long_line_with_tags(line, effective_max_length, open_tags)
                for part_chunk, part_tags in parts[:-1]:
                    result.append(part_chunk)
                last_chunk, open_tags = parts[-1]
                current_chunk = last_chunk + ('\n' if i < len(lines) - 1 else '')
                continue  # 跳过下面的标签更新，因为已经在切分函数中处理了

        else:
            current_chunk = test_chunk

        # 更新打开的标签列表
        open_tags = _update_open_tags(open_tags, line)

    # 添加最后一块
    if current_chunk:
        result.append(current_chunk.rstrip('\n'))

    # 添加分段标识
    if add_part_indicator and len(result) > 1:
        total = len(result)
        result = [f"[{i+1}/{total}]\n{chunk}" for i, chunk in enumerate(result)]

    return result


def _parse_tag(tag_str):
    """
    解析 HTML 标签，提取标签名和属性

    Args:
        tag_str: 标签字符串，如 '<font color="info">' 或 '</font>'

    Returns:
        (tag_name, attributes, is_closing) 元组
        如 ('font', 'color="info"', False) 或 ('font', '', True)
    """
    tag_str = tag_str.strip('<>')
    is_closing = tag_str.startswith('/')
    if is_closing:
        tag_str = tag_str[1:]

    # 分离标签名和属性
    parts = tag_str.split(None, 1)
    tag_name = parts[0].lower() if parts else ''
    attributes = parts[1] if len(parts) > 1 else ''

    return tag_name, attributes, is_closing


def _update_open_tags(open_tags, text):
    """
    根据文本内容更新打开的标签列表

    Args:
        open_tags: 当前打开的标签列表
        text: 要分析的文本

    Returns:
        更新后的打开标签列表
    """
    # 复制列表，避免修改原列表
    tags = list(open_tags)

    # 查找所有 HTML 标签
    tag_pattern = re.compile(r'<(/?)(\w+)([^>]*)>')
    for match in tag_pattern.finditer(text):
        is_closing = match.group(1) == '/'
        tag_name = match.group(2).lower()
        attributes = match.group(3).strip()

        if is_closing:
            # 闭合标签：从列表中移除最近的同名标签
            for j in range(len(tags) - 1, -1, -1):
                if tags[j][0] == tag_name:
                    tags.pop(j)
                    break
        else:
            # 开始标签：添加到列表
            # 跳过自闭合标签（如 <br/>, <img/>）
            if not attributes.endswith('/'):
                tags.append((tag_name, attributes))

    return tags


def _generate_closing_tags(open_tags):
    """
    生成闭合标签字符串（逆序闭合）

    Args:
        open_tags: 打开的标签列表

    Returns:
        闭合标签字符串，如 '</font></div>'
    """
    if not open_tags:
        return ""
    return ''.join(f'</{tag[0]}>' for tag in reversed(open_tags))


def _generate_opening_tags(open_tags):
    """
    生成开始标签字符串（正序打开）

    Args:
        open_tags: 打开的标签列表

    Returns:
        开始标签字符串，如 '<div><font color="info">'
    """
    if not open_tags:
        return ""
    result = []
    for tag_name, attributes in open_tags:
        if attributes:
            result.append(f'<{tag_name} {attributes}>')
        else:
            result.append(f'<{tag_name}>')
    return ''.join(result)


def _split_long_line_with_tags(line, max_length, open_tags):
    """
    安全地切分过长的单行，保护标签对完整性

    Args:
        line: 待切分的行
        max_length: 最大字节长度
        open_tags: 当前打开的标签列表

    Returns:
        列表，每个元素是 (chunk, updated_open_tags) 元组
    """
    if len(line.encode('utf-8')) <= max_length:
        updated_tags = _update_open_tags(open_tags, line)
        return [(line, updated_tags)]

    result = []
    current = ""
    current_tags = list(open_tags)
    i = 0

    while i < len(line):
        char = line[i]

        # 检测到 HTML 标签开始
        if char == '<':
            tag_end = line.find('>', i)
            if tag_end != -1:
                tag = line[i:tag_end + 1]

                # 计算加上标签后的长度（包括可能需要的闭合标签）
                test = current + tag
                closing = _generate_closing_tags(current_tags)
                test_with_closing = test + closing

                if len(test_with_closing.encode('utf-8')) > max_length and current:
                    # 需要切分：先闭合当前标签，保存当前块
                    closed_chunk = current + _generate_closing_tags(current_tags)
                    result.append((closed_chunk, list(current_tags)))

                    # 重新打开标签，开始新块
                    reopening = _generate_opening_tags(current_tags)
                    current = reopening + tag
                else:
                    current = test

                # 更新标签状态
                current_tags = _update_open_tags(current_tags, tag)
                i = tag_end + 1
                continue

        # 普通字符
        test = current + char
        closing = _generate_closing_tags(current_tags)
        test_with_closing = test + closing

        if len(test_with_closing.encode('utf-8')) > max_length and current:
            # 需要切分
            closed_chunk = current + closing
            result.append((closed_chunk, list(current_tags)))

            # 重新打开标签
            reopening = _generate_opening_tags(current_tags)
            current = reopening + char
        else:
            current = test
        i += 1

    if current:
        result.append((current, current_tags))

    return result if result else [(line, open_tags)]


def _split_long_line_safely(line, max_length):
    """
    安全地切分过长的单行（兼容旧接口）

    Args:
        line: 待切分的行
        max_length: 最大字节长度

    Returns:
        切分后的字符串列表
    """
    parts = _split_long_line_with_tags(line, max_length, [])
    return [chunk for chunk, _ in parts]



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

import httpx
import re
import logging
from bs4 import BeautifulSoup
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class BookSourceParser:
    """
    通用书源解析器，兼容阅读APP书源格式。
    支持 ruleContent、ruleSearch、ruleToc、ruleBookInfo 等主流字段。
    """
    def __init__(self, rule: Dict[str, Any], site_url: str, user_agent: str = None):
        self.rule = rule
        self.site_url = site_url
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; PJH110 Build/SP1A.210812.016) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.103 Mobile Safari/537.36",
            "Referer": site_url
        }
        self.client = httpx.AsyncClient(headers=self.headers, timeout=10)

    async def get_html(self, url: str, method: str = "GET", data: Optional[Dict] = None) -> str:
        # 每次请求都自动加 Referer
        headers = self.headers.copy()
        headers["Referer"] = self.site_url
        try:
            if method == "POST":
                resp = await self.client.post(url, headers=headers, data=data)
            else:
                resp = await self.client.get(url, headers=headers)
            resp.raise_for_status()  # 检查HTTP响应状态
            resp.encoding = resp.apparent_encoding
            return resp.text
        except httpx.RequestError as e:
            logger.warning(f"网络请求失败: {e}")
            return ""
        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP 状态错误: {e.response.status_code} - {e.response.text}")
            return ""

    def _resolve_url(self, relative_url: str) -> str:
        """
        将相对 URL 解析为绝对 URL。
        """
        if relative_url and not relative_url.startswith("http"):
            return self.site_url.rstrip("/") + "/" + relative_url.lstrip("/")
        return relative_url

    async def parse_content(self, chapter_url: str) -> Dict[str, Any]:
        """
        按 ruleContent 解析章节内容，自动处理分页、正则替换、标题。增加最大翻页次数限制。
        """
        rule = self.rule.get("ruleContent", {})
        content_selector = rule.get("content")
        next_selector = rule.get("nextContentUrl")
        replace_regex = rule.get("replaceRegex")
        title_selector = rule.get("title")
        content = ""
        title = ""
        url = chapter_url
        max_pages = 3
        page_count = 0
        while url and page_count < max_pages:
            html = await self.get_html(url)
            soup = BeautifulSoup(html, "html.parser")
            # 内容
            if content_selector:
                part = self._select(soup, content_selector)
                if part:
                    content += part
            # 标题
            if title_selector and not title:
                title = self._select(soup, title_selector)
            # 下一页
            next_url = self._select(soup, next_selector) if next_selector else None
            url = self._resolve_url(next_url)
            page_count += 1
        # 内容清洗
        if replace_regex:
            content = re.sub(replace_regex, "", content)
        return {"title": title, "content": content.strip()}

    async def parse_search(self, search_url: str, key: str) -> list:
        """
        按 ruleSearch 解析搜索结果。
        """
        rule = self.rule.get("ruleSearch", {})
        url = search_url.replace("{{key}}", key)
        html = await self.get_html(url)
        soup = BeautifulSoup(html, "html.parser")
        book_list_selector = rule.get("bookList")
        book_list = []
        if book_list_selector:
            items = soup.select(book_list_selector)
            for item in items:
                name = self._select(item, rule.get("name"))
                author = self._select(item, rule.get("author"))
                intro = self._select(item, rule.get("intro"))
                book_url = self._select(item, rule.get("bookUrl"))
                cover_url = self._select(item, rule.get("coverUrl"))
                book_list.append({
                    "name": name,
                    "author": author,
                    "intro": intro,
                    "book_url": self._resolve_url(book_url),
                    "cover_url": self._resolve_url(cover_url)
                })
        return book_list

    async def parse_toc(self, toc_url: str) -> list:
        """
        按 ruleToc 解析目录。
        """
        rule = self.rule.get("ruleToc", {})
        html = await self.get_html(toc_url)
        soup = BeautifulSoup(html, "html.parser")
        chapter_list_selector = rule.get("chapterList")
        chapters = []
        if chapter_list_selector:
            items = soup.select(chapter_list_selector)
            for item in items:
                name = self._select(item, rule.get("chapterName"))
                url = self._select(item, rule.get("chapterUrl"))
                chapters.append({"name": name, "url": self._resolve_url(url)})
        return chapters

    async def parse_book_info(self, info_url: str) -> Dict[str, Any]:
        """
        按 ruleBookInfo 解析书籍信息。
        """
        rule = self.rule.get("ruleBookInfo", {})
        html = await self.get_html(info_url)
        soup = BeautifulSoup(html, "html.parser")
        intro = self._select(soup, rule.get("intro"))
        return {"intro": intro}

    async def parse_find(self, find_url: str) -> list:
        """
        按 ruleFind 解析分类列表。
        """
        rule = self.rule.get("ruleFind", {})
        html = await self.get_html(find_url)
        soup = BeautifulSoup(html, "html.parser")
        find_list_selector = rule.get("findList")
        finds = []
        if find_list_selector:
            items = soup.select(find_list_selector)
            for item in items:
                name = self._select(item, rule.get("findName"))
                url = self._select(item, rule.get("findUrl"))
                finds.append({"name": name, "url": self._resolve_url(url)})
        return finds

    def _select(self, soup, selector: str) -> str:
        """
        支持 CSS 选择器（如 a > h2@text），a@href 属性提取，以及原有 id.xx@text、class.xx@html、tag@text。
        """
        if not selector:
            return ""
        # 支持正则清洗
        if "##" in selector:
            sel, regex = selector.split("##", 1)
            val = self._select(soup, sel)
            return re.sub(regex, "", val)
        parts = selector.split("@")
        if len(parts) == 2:
            sel, typ = parts
            # 优先用 CSS 选择器
            nodes = soup.select(sel)
            node = nodes[0] if nodes else None
            if not node:
                # 回退到原有 find 逻辑
                if sel.startswith("id."):
                    node = soup.find(id=sel[3:])
                elif sel.startswith("class."):
                    node = soup.find(class_=sel[6:])
                else:
                    node = soup.find(sel)
            if node:
                if typ == "text":
                    return node.get_text(strip=True)
                elif typ == "html":
                    return str(node)
                else:
                    # 支持 a@href 这种属性提取
                    return node.get(typ, "")
            else:
                logger.debug(f"选择器 '{selector}' 未找到任何节点。在 URL: {soup.prettify()}") # 添加日志，并打印部分HTML以便调试
        return ""

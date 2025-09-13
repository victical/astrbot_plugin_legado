import random
import json
from astrbot.api.star import Context, Star, register
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api import logger

# 导入我们创建的解析器
from .booksource_parser import BookSourceParser

# 用于渲染小说的 HTML + Jinja2 模板
NOVEL_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {
            font-family: 'Microsoft YaHei', 'SimSun', sans-serif;
            background-color: #f9f9f9;
            margin: 0;
            padding: 10px; /* 减小 body 填充 */
            width: 1280px;
            box-sizing: border-box; /* 确保 padding 包含在 width 内 */
        }
        .container {
            border: 1px solid #eee;
            border-radius: 10px;
            padding: 20px; /* 减小 container 填充 */
            background-color: white;
            box-shadow: 0 4px 8px rgba(0,0,0,0.05);
            width: 100%; /* 确保容器占满可用宽度 */
            box-sizing: border-box;
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 10px;
        }
        .meta {
            text-align: center;
            color: #888;
            font-size: 16px;
            margin-bottom: 30px;
        }
        p {
            font-size: 22px;
            line-height: 1.8;
            color: #444;
            text-indent: 2em;
            margin-top: 0;
            margin-bottom: 1em;
            word-break: break-word; /* 强制长单词换行 */
            max-width: 100%; /* 确保段落不超过父容器宽度 */
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>{{ title }}</h1>
        <div class="meta">
            <span>{{ book_name }}</span> / <span>{{ author }}</span>
        </div>
        {% for paragraph in paragraphs %}
            <p>{{ paragraph }}</p>
        {% endfor %}
    </div>
</body>
</html>
"""

@register("astrbot_plugin_legado", "Victical", "通用书源小说插件", "0.0.1", "https://github.com/victical/astrbot_plugin_legado")
class LegadoNovelPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.context = context
        
        # 从配置加载，如果失败则使用默认值
        config = config or {}
        legado_config = config.get("legado", {})
        
        self.site_url = legado_config.get("site_url", "http://3g.shugelou.org")
        self.find_url = legado_config.get("find_url", "http://3g.shugelou.org/fenlei.html")
        self.user_agent = legado_config.get("user_agent", "Mozilla/5.0 (Linux; Android 14; PJH110 Build/SP1A.210812.016) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.103 Mobile Safari/537.36")
        
        default_rules = {
            "ruleSearch": {"author": "p@text##.*</a>", "bookList": ".cover p.line", "bookUrl": "a@href", "name": "a@text"},
            "ruleToc": {"chapterList": ".chapter li", "chapterName": "a@text", "chapterUrl": "a@href"},
            "ruleContent": {"content": "id.nr1@html##{{chapter.title}}.*|最新网址：3g\\.shugelou\\.org", "nextContentUrl": "id.pt_next@href", "replaceRegex": "##\n（本章未完，请点击下一页继续阅读）\\n", "title": "id._bqgmb_h1@text"},
            "ruleFind": {"findList": ".content li", "findName": "a@text", "findUrl": "a@href"}
        }
        
        try:
            self.rule = json.loads(legado_config.get("rules", "{}")) or default_rules
        except json.JSONDecodeError:
            logger.error("解析书源规则失败，将使用默认规则。")
            self.rule = default_rules

        self.parser = BookSourceParser(self.rule, self.site_url, self.user_agent)
        self.last_sent = None

    def get_random_novel_chapter(self):
        try:
            logger.info("开始获取小说分类...")
            categories = self.parser.parse_find(self.find_url)
            if not categories:
                logger.error("获取分类失败。")
                return None

            random_category = random.choice(categories)
            logger.info(f"随机选择分类: {random_category['name']}")

            html = self.parser.get_html(random_category['url'])
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            
            rule_search = self.rule.get("ruleSearch", {})
            book_list_selector = rule_search.get("bookList")
            books = []
            if book_list_selector:
                items = soup.select(book_list_selector)
                for item in items:
                    name = self.parser._select(item, rule_search.get("name"))
                    author = self.parser._select(item, rule_search.get("author")).strip("/")
                    book_url = self.parser._select(item, rule_search.get("bookUrl"))
                    if book_url and not book_url.startswith("http"):
                        book_url = self.site_url.rstrip("/") + "/" + book_url.lstrip("/")
                    books.append({"name": name, "author": author, "url": book_url})
            
            if not books:
                logger.error(f"在分类 '{random_category['name']}' 下未找到任何小说。")
                return None

            random_book = random.choice(books)
            logger.info(f"随机选择小说: {random_book['name']}")

            chapters = self.parser.parse_toc(random_book['url'])
            if not chapters:
                logger.error(f"获取小说 '{random_book['name']}' 的章节列表失败。")
                return None

            first_chapter = None
            # 尝试使用更灵活的正则表达式匹配“第一章”或“第1章”
            import re
            for chap in chapters:
                if re.search(r"第[一1]章", chap["name"]):
                    first_chapter = chap
                    break
            
            # 如果仍然没有找到，则回退到列表的最后一个元素（通常是倒序列表的第一章）
            if not first_chapter:
                first_chapter = chapters[-1]
            
            logger.info(f"找到第一章: {first_chapter['name']}")

            chapter_content = self.parser.parse_content(first_chapter['url'])
            
            return {
                "name": random_book["name"],
                "author": random_book["author"],
                "title": chapter_content.get("title"),
                "text": chapter_content.get("content")
            }

        except Exception as e:
            logger.error(f"获取随机小说时发生错误: {e}", exc_info=e)
            return None

    @filter.command("随机小说")
    async def random_novel(self, event: AstrMessageEvent, text: str = ""):
        '''随机发送一本小说第一章的图片'''
        yield event.plain_result("正在随机寻找一本小说，请稍候...") # 提供即时反馈
        book_data = self.get_random_novel_chapter()
        
        if not book_data:
            yield event.plain_result("获取小说失败，请稍后再试。")
            return
        
        from bs4 import BeautifulSoup
        text_content = BeautifulSoup(book_data["text"], "html.parser").get_text("\n")
        paragraphs = [p.strip() for p in text_content.split("\n") if p.strip()]
        
        template_data = {
            "title": book_data["title"],
            "book_name": book_data["name"],
            "author": book_data["author"],
            "paragraphs": paragraphs
        }
        
        image_url = await self.html_render(NOVEL_TEMPLATE, template_data)
        
        self.last_sent = book_data
        yield event.image_result(image_url)

    @filter.command("小说信息")
    async def novel_info(self, event: AstrMessageEvent, text: str = ""):
        '''获取上一本随机小说的信息'''
        if self.last_sent:
            yield event.plain_result(f"书名：《{self.last_sent['name']}》\n作者：{self.last_sent['author']}")
        else:
            yield event.plain_result("暂无小说信息，请先发送 /随机小说")

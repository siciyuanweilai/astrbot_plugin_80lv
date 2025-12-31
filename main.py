import asyncio
import traceback
from typing import List, Dict
import aiohttp
import re
import datetime

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.api.platform import MessageType

from .spider import LvSpider
from .data import save_data, load_data

@register("astrbot_plugin_80lv", "Soulter", "80.lv 文章推送插件", "1.0.0")
class LvPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # === 配置读取 ===
        network_config = config.get("network_config", {})
        self.per_page = network_config.get("per_page", 1) 
        self.network_interval = network_config.get("interval", 2)
        
        display_config = config.get("display_config", {})
        self.show_thumbnail = display_config.get("show_thumbnail", True)
        self.show_excerpt = display_config.get("show_excerpt", False)
        self.show_author = display_config.get("show_author", False)
        self.fold_threshold = display_config.get("fold", 2)
        
        monitor_config = config.get("monitor_config", {})
        self.monitor_enabled = monitor_config.get("enabled", False)
        self.monitor_interval = monitor_config.get("interval", 300)
        
        receiver = monitor_config.get("receiver", {})
        self.monitor_receiver_groups = receiver.get("groups", [])
        self.monitor_receiver_users = receiver.get("users", [])
        
        filter_config = config.get("filter_config", {})
        self.filter_keywords = filter_config.get("keywords", [])
        self.filter_exclude = filter_config.get("exclude_keywords", [])
        self.filter_categories = filter_config.get("categories", [])
        self.filter_exclude_categories = filter_config.get("exclude_categories", [])

        self.spider = LvSpider()
        self.is_checking = False
        
        if self.monitor_enabled:
            logger.info(f"80.lv 监控已启动 (每次限制 {self.per_page} 篇)")
            asyncio.create_task(self.monitor_task())

    async def monitor_task(self):
        while self.monitor_enabled:
            await asyncio.sleep(self.monitor_interval)
            try:
                await self._check_updates()
            except Exception as e:
                logger.error(f"80.lv 监控任务出错: {e}")
                logger.error(traceback.format_exc())

    @filter.command_group("lv")
    def lv(self):
        pass

    @lv.command("start")
    async def start(self, event: AstrMessageEvent):
        self.monitor_enabled = True
        if "monitor_config" not in self.config:
            self.config["monitor_config"] = {}
        self.config["monitor_config"]["enabled"] = True
        self.config.save_config()
        asyncio.create_task(self.monitor_task())
        yield event.plain_result(f"80.lv 监控已开启，检查间隔 {self.monitor_interval} 秒。")

    @lv.command("stop")
    async def stop(self, event: AstrMessageEvent):
        self.monitor_enabled = False
        if "monitor_config" in self.config:
            self.config["monitor_config"]["enabled"] = False
            self.config.save_config()
        yield event.plain_result("80.lv 监控已停止。")

    @lv.command("check")
    async def check(self, event: AstrMessageEvent):
        await self.context.send_message(event.unified_msg_origin, MessageChain([Comp.Plain(f"正在检查 80.lv 更新 (限制 {self.per_page} 篇)...")]))
        try:
            await self._check_updates(event)
        except Exception as e:
            logger.error(f"手动检查出错: {traceback.format_exc()}")
            yield event.plain_result(f"检查出错: {e}")

    async def _translate_content(self, title: str, excerpt: str) -> tuple[str, str]:
        provider = self.context.get_using_provider()
        if not provider:
            return title, excerpt
        
        clean_excerpt = re.sub('<[^<]+?>', '', excerpt)[:500]
        if not clean_excerpt:
            clean_excerpt = "No description."

        prompt = f"""
你是一个专业的游戏美术与技术文章翻译助手。请将以下来自 80.lv 的文章元数据翻译成简体中文。
要求：
1. 游戏开发术语（如 Mesh, Shader, UE5, Workflow 等）请保留英文或使用行业标准译名。
2. 语气简洁专业。
3. 严格按照下方格式返回。

格式要求：
TITLE_CN: <翻译后的标题>
EXCERPT_CN: <翻译后的摘要>

待翻译内容：
Title: {title}
Excerpt: {clean_excerpt}
"""
        try:
            response = await provider.text_chat(prompt, session_id=None)
            result_text = response.completion_text
            new_title = title
            new_excerpt = clean_excerpt
            t_match = re.search(r"TITLE_CN:\s*(.+)", result_text)
            if t_match: new_title = t_match.group(1).strip()
            e_match = re.search(r"EXCERPT_CN:\s*(.+)", result_text, re.DOTALL)
            if e_match: new_excerpt = e_match.group(1).strip()
            return new_title, new_excerpt
        except Exception as e:
            logger.error(f"LLM 翻译失败: {e}")
            return title, excerpt

    async def _check_updates(self, event: AstrMessageEvent = None):
        if self.is_checking:
            if event: await self.context.send_message(event.unified_msg_origin, MessageChain([Comp.Plain("检查正在进行中。")]))
            return
        self.is_checking = True
        try:
            known_articles = load_data()
            known_ids = {str(item["id"]) for item in known_articles} 
            async with aiohttp.ClientSession() as session:
                latest_articles = await self.spider.get_articles(session, page=1, total=self.per_page)
            
            if len(latest_articles) > self.per_page:
                latest_articles = latest_articles[:self.per_page]
            
            new_articles = []
            valid_fetched_articles = [] 
            for art in latest_articles:
                art_id = str(art["id"])
                valid_fetched_articles.append(art)
                if art_id not in known_ids:
                    if self._filter_article(art):
                        new_articles.append(art)
            
            if valid_fetched_articles:
                all_data = valid_fetched_articles + [x for x in known_articles if str(x["id"]) not in [str(a["id"]) for a in valid_fetched_articles]]
                save_data(all_data[:200])
            
            if not new_articles:
                if event: await self.context.send_message(event.unified_msg_origin, MessageChain([Comp.Plain("没有发现新文章。")]))
                return
            
            if len(new_articles) > self.per_page:
                new_articles = new_articles[:self.per_page]
            
            chain_list = []
            logger.info(f"检测到 {len(new_articles)} 篇新文章，准备进行翻译和推送...")
            
            for art in new_articles:
                original_title = art.get("title", "")
                original_excerpt = art.get("excerpt", "")
                trans_title, trans_excerpt = await self._translate_content(original_title, original_excerpt)
                art["title"] = trans_title
                art["excerpt"] = trans_excerpt
                
                chain = await self._make_msg_chain(art)
                chain_list.append(chain)
            
            await self._post_articles(chain_list, event)
        finally:
            self.is_checking = False

    def _filter_article(self, art: Dict) -> bool:
        """
        过滤文章逻辑：
        1. 排除关键词
        2. 排除分类标签
        3. 包含关键词
        4. 包含分类标签
        """
        title = art.get("title", "")
        desc = art.get("excerpt", "")
        cats = art.get("categories", [])
        
        # 排除关键词
        if self.filter_exclude:
            if any(k.lower() in title.lower() or k.lower() in desc.lower() for k in self.filter_exclude):
                return False

        # 排除分类 - 优先级高
        if self.filter_exclude_categories:
            config_exclude_set = {str(c).lower().strip() for c in self.filter_exclude_categories if c}
            article_cats_set = {str(c).lower().strip() for c in cats if c}
            
            # 如果两者有交集（即文章包含任何一个需要排除的分类），则屏蔽
            if config_exclude_set & article_cats_set:
                return False
        
        # 包含关键词
        match_keyword = True
        if self.filter_keywords:
            match_keyword = any(k.lower() in title.lower() or k.lower() in desc.lower() for k in self.filter_keywords)
        
        # 包含分类
        match_category = True
        if self.filter_categories:
            config_cats_set = {str(c).lower().strip() for c in self.filter_categories if c}
            article_cats_set = {str(c).lower().strip() for c in cats if c}
            if not (config_cats_set & article_cats_set):
                match_category = False
                
        # 如果关键词和分类都设置了，必须同时满足
        if self.filter_keywords and self.filter_categories:
            return match_keyword and match_category
        
        # 如果只设置了关键词
        if self.filter_keywords:
            return match_keyword
            
        # 如果只设置了分类
        if self.filter_categories:
            return match_category

        # 如果都没设置，允许通过
        return True

    async def _make_msg_chain(self, art: Dict) -> List:
        """构建单篇文章的消息组件列表"""
        
        raw_date = art.get("date", "")
        date_str = str(raw_date) 

        try:
            # %d = 日(29), %B = 月份全称(December), %Y = 年(2025)
            # 解析成功后格式化为: 2025-12-29
            dt = datetime.datetime.strptime(str(raw_date).strip(), "%d %B %Y")
            date_str = dt.strftime("%Y-%m-%d")
        except:
            pass

        render_data = {
            "title": art.get("title", "No Title"),
            "image": art.get("thumbnail", ""),
            "author": art.get("author", "80.lv"),
            "author_avatar": art.get("author_avatar", ""),
            "excerpt": art.get("excerpt", "").strip(),
            "date": date_str,
            "categories": art.get("categories", []),
        }

        try:
            img_url = await self.html_render(TMPL, render_data)
            
            url = self.spider.build_article_url(art.get("slug", ""))
            
            chain = [
                Comp.Image.fromURL(img_url),
                Comp.Plain(f"{url}")
            ]
            return chain
        except Exception as e:
            logger.error(f"HTML 渲染失败: {e}")
            return [Comp.Plain(f"{art.get('title')}\n{url}")]

    async def _post_articles(self, chain_list: List, event: AstrMessageEvent = None):
        platform = self.context.get_platform("aiocqhttp")
        if not platform:
            if event:
                for chain in chain_list:
                    await self.context.send_message(event.unified_msg_origin, MessageChain(chain))
            return
        
        client = platform.get_client()
        def build_node_content(chain):
            content = []
            for item in chain:
                if isinstance(item, Comp.Image):
                    content.append({"type": "image", "data": {"file": item.file}})
                elif isinstance(item, Comp.Plain):
                    content.append({"type": "text", "data": {"text": item.text}})
                elif isinstance(item, Comp.Video):
                    content.append({"type": "video", "data": {"file": item.file}})
            return content

        async def send_fold(target_type, target_id):
            messages = []
            for chain in chain_list:
                messages.append({ "type": "node", "data": { "content": build_node_content(chain) } })
            payloads = {
                "messages": messages,
                "news": [{"text": f"本次推送了 {len(chain_list)} 篇新文章"}],
                "prompt": f"活到老学到老",
                "summary": "80.lv 提供来自游戏工作室的独家见解",
                "source": "80.lv 发布了新文章",
            }
            if target_type == "group":
                payloads["group_id"] = target_id
                await client.call_action("send_group_forward_msg", **payloads)
            else:
                payloads["user_id"] = target_id
                await client.call_action("send_private_forward_msg", **payloads)

        async def send_unfold(target_type, target_id):
            for chain in chain_list:
                payload = {"message": build_node_content(chain)}
                if target_type == "group":
                    payload["group_id"] = target_id
                    await client.call_action("send_group_msg", **payload)
                else:
                    payload["user_id"] = target_id
                    await client.call_action("send_private_msg", **payload)
                await asyncio.sleep(self.network_interval)

        if event is not None:
            if event.get_platform_name() != "aiocqhttp":
                for chain in chain_list: await self.context.send_message(event.unified_msg_origin, MessageChain(chain))
                return
            msg_type = event.get_message_type()
            target_id = str(event.get_session_id())
            is_group = msg_type == MessageType.GROUP_MESSAGE
            target_type = "group" if is_group else "private"
            if len(chain_list) > self.fold_threshold: await send_fold(target_type, target_id)
            else: await send_unfold(target_type, target_id)
        else:
            if not self.monitor_receiver_groups and not self.monitor_receiver_users: return
            for group_id in self.monitor_receiver_groups:
                gid = str(group_id)
                if len(chain_list) > self.fold_threshold: await send_fold("group", gid)
                else: await send_unfold("group", gid)
            for user_id in self.monitor_receiver_users:
                uid = str(user_id)
                if len(chain_list) > self.fold_threshold: await send_fold("private", uid)
                else: await send_unfold("private", uid)

TMPL = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: "ChillRoundM", Arial, sans-serif; }
        
        body { 
            background-color: #fff;
            width: 100%; 
        }
        
        .card {
            width: 100%;
            overflow: hidden;
            background: #fff;
            display: flex;
            flex-direction: column;
        }

        .cover-container {
            width: 100%;
            line-height: 0;
            margin-bottom: 40px;
        }

        .cover-img {
            width: 100%;
            height: auto;
            display: block;
        }

        .content {
            padding: 0 50px 50px 50px;
            flex-grow: 1;
            display: flex;
            flex-direction: column;
        }

        .author-row {
            display: flex;
            align-items: center;
            padding-bottom: 10px; 
            margin-bottom: 20px;  
            border-bottom: 3px solid #f0f0f0; 
        }
        
        .author-avatar {
            width: 80px; 
            height: 80px;
            border-radius: 50%;
            object-fit: cover;
            margin-right: 24px;
            border: 2px solid #f0f0f0;
            flex-shrink: 0;
        }

        .author-avatar-placeholder {
            width: 80px; 
            height: 80px;
            border-radius: 50%;
            background-color: #e53935;
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 36px;
            margin-right: 24px;
            flex-shrink: 0;
        }

        .author-name {
            font-size: 36px;
            font-weight: 600;
            color: #333;
        }

        /* 标题 */
        .title {
            font-size: 60px;
            font-weight: bold;
            line-height: 1.25;
            color: #111;
            margin-bottom: 30px;
            word-wrap: break-word;
        }

        /* 标签栏  */
        .tags {
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            margin-bottom: 40px;
        }

        .tag {
            background: #f4f4f4;
            padding: 10px 20px;
            border-radius: 8px;
            font-size: 24px;
            color: #666;
            font-weight: 500;
        }

        .excerpt {
            font-size: 38px; 
            line-height: 1.6;
            color: #555;
            text-align: justify;
            word-wrap: break-word;
            
            padding-bottom: 30px;
            margin-bottom: 10px;
            border-bottom: 3px solid #f0f0f0;
        }

        .footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-top: 0;
            color: #999;
            font-size: 28px;
        }
        
        .logo-mark {
            font-weight: bold;
            color: #e53935;
            font-size: 32px;
        }
    </style>
</head>
<body>
    <div class="card">
        {% if image %}
        <div class="cover-container">
            <img src="{{ image }}" class="cover-img" />
        </div>
        {% endif %}
        
        <div class="content">
            <!-- 1. 作者 -->
            <div class="author-row">
                {% if author_avatar %}
                    <img src="{{ author_avatar }}" class="author-avatar" />
                {% else %}
                    <div class="author-avatar-placeholder">{{ author[0] | upper }}</div>
                {% endif %}
                <div class="author-name">{{ author }}</div>
            </div>

            <!-- 2. 标题 -->
            <div class="title">{{ title }}</div>

            <!-- 3. 标签 -->
            {% if categories %}
            <div class="tags">
                {% for cat in categories %}
                <span class="tag">{{ cat }}</span>
                {% endfor %}
            </div>
            {% endif %}

            <!-- 4. 摘要 -->
            {% if excerpt %}
            <div class="excerpt">
                <span style="font-weight: bold; color: #333;">摘要:</span> {{ excerpt }}
            </div>
            {% endif %}

            <!-- 5. 底部日期与Logo -->
            <div class="footer">
                <div class="date">发布于：{{ date }}</div>
                <div class="logo-mark">80.lv</div>
            </div>
        </div>
    </div>
</body>
</html>
"""

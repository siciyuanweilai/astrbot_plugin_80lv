import aiohttp
from typing import List, Dict
from astrbot.api import logger

class LvSpider:
    def __init__(self, proxy: str = None):
        # 80.lv API https://80.lv/api/articles/list
        self.base_api = "https://80.lv/api/articles/list"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://80.lv/",
            "Accept": "application/json"
        }

    async def get_articles(self, session: aiohttp.ClientSession, page: int = 1, total: int = 10) -> List[Dict]:
        """获取文章列表"""
        params = {
            "limit": total,
            "offset": (page - 1) * total
        }
        
        try:
            async with session.get(self.base_api, params=params, headers=self.headers) as resp:
                if resp.status != 200:
                    logger.error(f"80.lv API returned {resp.status}")
                    return []
                
                data = await resp.json()
                items = data.get("items", [])
                
                result = []
                for item in items:
                    parsed = self._parse_article(item)
                    if parsed:
                        result.append(parsed)
                return result
                
        except Exception as e:
            logger.error(f"Failed to fetch 80.lv articles: {e}")
            return []

    def _parse_article(self, data: Dict) -> Dict:
        """解析单篇文章数据"""
        try:
            # 解析 ID 和 Slug
            art_id = data.get("id")
            slug = data.get("slug")
            if not art_id or not slug:
                return {}

            # 解析图片
            image_data = data.get("image", {}) or {}
            preview_data = data.get("preview", {}) or {}
            
            thumbnail = image_data.get("original")
            if not thumbnail:
                thumbnail = preview_data.get("original")
            if not thumbnail:
                thumbnail = image_data.get("src2x") or image_data.get("src")
            
            # 解析作者及头像
            author_data = data.get("author", {})
            author_name = author_data.get("name", "Unknown") if isinstance(author_data, dict) else "Unknown"
            
            # 提取头像逻辑
            author_avatar = ""
            if isinstance(author_data, dict):
                avatar_data = author_data.get("avatar", {})
                if isinstance(avatar_data, dict):
                    author_avatar = avatar_data.get("original") or avatar_data.get("src2x") or avatar_data.get("src")

            # 4. 解析分类 (Tags)
            # api.json 结构: tags: [{"name": "Interviews", ...}, {"name": "Props", ...}]
            tags = data.get("tags", [])
            categories = []
            if isinstance(tags, list):
                for t in tags:
                    if isinstance(t, dict):
                        name = t.get("name")
                        if name:
                            categories.append(name)

            # 5. 解析摘要
            excerpt = data.get("description", "")

            return {
                "id": art_id,
                "title": data.get("title", ""),
                "slug": slug,
                "author": author_name,
                "author_avatar": author_avatar,
                "date": data.get("date", ""),
                "thumbnail": thumbnail,
                "excerpt": excerpt,
                "categories": categories
            }
        except Exception as e:
            logger.error(f"Error parsing article item: {e}")
            return {}

    def build_article_url(self, slug: str) -> str:
        return f"https://80.lv/articles/{slug}"

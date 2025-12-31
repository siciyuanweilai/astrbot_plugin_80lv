import json
import os


def save_data(data, filename="data.json"):
    """保存数据到 JSON 文件"""
    file_path = os.path.join(
        "data", "plugin_data", "astrbot_plugin_80lv", "known_articles", filename
    )
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def load_data(filename="data.json"):
    """从 JSON 文件加载数据"""
    file_path = os.path.join(
        "data", "plugin_data", "astrbot_plugin_80lv", "known_articles", filename
    )
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump([], file, ensure_ascii=False, indent=4)
            return []

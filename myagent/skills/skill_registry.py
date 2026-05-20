from typing import Callable, Dict, Any


class SkillRegistry:
    """
    Skill 注册表。

    作用：
    1. 注册 skill 名称和对应函数
    2. 根据 skill 名称调用对应函数
    3. 避免 Agent 直接接触底层小车动作
    """

    def __init__(self):
        self._skills: Dict[str, Callable[..., Dict[str, Any]]] = {}

    def register(self, name: str, func: Callable[..., Dict[str, Any]]):
        if not name:
            raise ValueError("skill name 不能为空")

        if name in self._skills:
            raise ValueError(f"skill 已存在: {name}")

        self._skills[name] = func

    def has_skill(self, name: str) -> bool:
        return name in self._skills

    def list_skills(self):
        return list(self._skills.keys())

    def call(self, name: str, **kwargs) -> Dict[str, Any]:
        if name not in self._skills:
            raise ValueError(f"未知 skill: {name}")

        return self._skills[name](**kwargs)

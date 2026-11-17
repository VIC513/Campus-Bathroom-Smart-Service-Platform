# -*- coding: utf-8 -*-
"""
跨区域智能分流推荐算法
对应项目 PDF 要求：当某区域浴室过于拥挤时，根据实时等待时间与步行距离，
自动向用户推荐邻近且空闲的浴室。

阈值依据问卷 Q6：「邻近区域空闲且步行仅需 3 分钟，是否愿意前往？」
  -> 只有当“步行时间 + 新区域等待时间”显著小于当前等待时间时才推荐
"""

import math


class CrossAreaRecommender:
    """
    跨区域分流推荐器
    areas 配置: {area_id: {"walk_min": 步行分钟数, "stalls": 隔间数}}
    """

    def __init__(self, areas: dict, walk_threshold_min: int = 3,
                 min_improve_min: float = 5.0):
        """
        walk_threshold_min: 可接受的最大步行时间（问卷 Q6 设定为 3 分钟）
        min_improve_min:    至少能节省多少分钟才值得推荐（防止无谓折腾）
        """
        self.areas = areas
        self.walk_threshold = walk_threshold_min
        self.min_improve = min_improve_min

    # ---------- 单个区域的综合代价 ----------
    def _cost(self, wait_min: float, walk_min: float) -> float:
        """综合代价 = 等待时间 + 步行时间（步行按 1:1 折算成时间成本）"""
        return wait_min + walk_min

    # ---------- 推荐 ----------
    def recommend(self, current_area: str, current_wait_min: float,
                  area_waits: dict) -> dict:
        """
        current_area:   用户当前所在区域 id
        current_wait_min: 当前区域预计等待（分钟，由 WaitTimePredictor 给出）
        area_waits:     {area_id: 预计等待分钟}，须包含所有候选区域
        返回:
          {"recommend": bool, "target_area": str|None, "reason": str,
           "saving_min": float, "ranking": [...]}
        """
        ranking = []
        for area_id, wait in area_waits.items():
            walk = self.areas.get(area_id, {}).get("walk_min", math.inf)
            ranking.append({
                "area_id": area_id,
                "wait_min": wait,
                "walk_min": walk,
                "cost": self._cost(wait, walk),
            })
        ranking.sort(key=lambda x: x["cost"])

        current_cost = self._cost(current_wait_min, 0.0)  # 本区域步行=0
        best = next((c for c in ranking if c["area_id"] != current_area), None)

        if best is None:
            return {"recommend": False, "target_area": None,
                    "reason": "无其他候选区域", "saving_min": 0.0,
                    "ranking": ranking}

        saving = current_cost - best["cost"]
        if best["walk_min"] > self.walk_threshold:
            return {"recommend": False, "target_area": None,
                    "reason": f"最近备选区域步行需 {best['walk_min']} 分钟，"
                              f"超过 {self.walk_threshold} 分钟阈值",
                    "saving_min": 0.0, "ranking": ranking}

        if saving >= self.min_improve:
            return {
                "recommend": True,
                "target_area": best["area_id"],
                "reason": f"{best['area_id']} 区等待 {best['wait_min']:.0f} 分钟"
                          f"+步行 {best['walk_min']:.0f} 分钟，"
                          f"比当前区域节省约 {saving:.0f} 分钟",
                "saving_min": round(saving, 1),
                "ranking": ranking,
            }
        return {"recommend": False, "target_area": None,
                "reason": f"跨区仅节省 {saving:.1f} 分钟，不值得折腾",
                "saving_min": round(saving, 1), "ranking": ranking}


# ================= 演示 =================
if __name__ == '__main__':
    # 三个区域浴室：A 为本楼，B 步行 3 分钟，C 步行 8 分钟
    areas = {
        "A": {"walk_min": 0, "stalls": 15},
        "B": {"walk_min": 3, "stalls": 10},
        "C": {"walk_min": 8, "stalls": 20},
    }
    rec = CrossAreaRecommender(areas)

    # 场景1: 晚高峰 A 区拥挤，B 区空闲 -> 应推荐 B
    print(rec.recommend("A", current_wait_min=40,
                        area_waits={"A": 40, "B": 5, "C": 2}))
    # 场景2: 各区域差别不大 -> 不推荐
    print(rec.recommend("A", current_wait_min=10,
                        area_waits={"A": 10, "B": 8, "C": 6}))

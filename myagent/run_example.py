import asyncio
from pathlib import Path

from DefenseAgent.agent import AgentConfig, ReActAgent


async def main():
    config = AgentConfig(
        profile=Path("./my_profile/profile.yaml"),
        use_tools=True,
        use_memory=False,
        use_compressor=False,
        use_rag=False,
    )

    prompt = """
现在进行一个 UrbanAgent 调度展示。

火情位置：A3 小区 5 号楼附近垃圾房，疑似小型明火。
火情等级：中等。y'f

当前有三辆消防小车：
F-01：位于城市广场，距离火点 2.8 km。
F-02：位于东湖社区消防点，距离火点 1.9 km。
F-03：位于产业园西门，距离火点 2.4 km。

候选路线：
Route-A：主干道，距离最短，但当前严重拥堵，预计 11 分钟。
Route-B：社区支路，距离略长，道路较窄，但消防小车可通过，预计 6 分钟。
Route-C：产业园外环路，通行顺畅，但绕行较远，预计 9 分钟。

请你作为 UrbanAgent，生成消防小车调度方案。
"""

    async with ReActAgent(config) as agent:
        result = await agent.run(
            prompt,
            max_steps=6,
        )
        print(result.final_answer)


if __name__ == "__main__":
    asyncio.run(main())
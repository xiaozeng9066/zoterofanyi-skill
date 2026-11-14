"""为三篇论文生成 glossary_meta.json（共享基础术语 + 各自专有术语）。"""
import json
import os
import sys

BASE = [
    ["obesity", "肥胖"],
    ["overweight", "超重"],
    ["overweight/obesity", "超重/肥胖"],
    ["childhood obesity", "儿童肥胖"],
    ["body mass index (BMI)", "体质指数（BMI）"],
    ["BMI z-score", "BMI z 评分"],
    ["school environment", "学校环境"],
    ["school-level", "学校层面"],
    ["individual-level", "个体层面"],
    ["multilevel", "多水平"],
    ["cross-sectional", "横断面"],
    ["prevalence", "患病率"],
    ["socioeconomic status", "社会经济地位"],
    ["physical activity", "体力活动"],
    ["sedentary behaviour", "久坐行为"],
    ["diet", "饮食"],
    ["food environment", "食物环境"],
    ["school canteen", "学校食堂"],
    ["vending machine", "自动售货机"],
    ["obesogenic", "致肥胖"],
    ["odds ratio (OR)", "比值比（OR）"],
    ["confidence interval (CI)", "置信区间（CI）"],
    ["statistically significant", "有统计学意义"],
    ["questionnaire", "问卷"],
    ["anthropometric", "人体测量"],
    ["waist circumference", "腰围"],
    ["policy", "政策"],
]

PAPERS = {
    "3EHBMRQR": {
        "title_zh": "欧洲儿童样本中的同伴效应与肥胖",
        "journal": "Economics and Human Biology 18:139–152 (IZA DP No. 9051)",
        "doi": "10.1016/j.ehb.2015.05.002",
        "extra": [
            ["peer effect", "同伴效应"],
            ["endogenous peer effect", "内生同伴效应"],
            ["contextual effect", "情境效应"],
            ["instrumental variable", "工具变量"],
            ["fixed effects", "固定效应"],
            ["ordinary least squares (OLS)", "普通最小二乘（OLS）"],
            ["reference group", "参照群体"],
            ["social multiplier", "社会乘数"],
            ["IDEFICS study", "IDEFICS 研究"],
            ["parental misperception", "父母认知偏差"],
        ],
    },
    "RL2P7Y6L": {
        "title_zh": "北京市小学生特定学校层面与个体层面特征与肥胖的关联",
        "journal": "Public Health Nutrition 23(10):1838–1845",
        "doi": "10.1017/S1368980019004592",
        "extra": [
            ["school-based obesity prevention index", "学校肥胖防控指数"],
            ["urban", "城市"],
            ["rural", "农村"],
            ["multilevel logistic regression", "多水平 logistic 回归"],
            ["school staff", "学校教职工"],
            ["primary school", "小学"],
        ],
    },
    "RB7BRXRA": {
        "title_zh": "研究者如何界定学校环境以判断其对儿童肥胖影响的系统综述",
        "journal": "Systematic Reviews (BMC) 2015",
        "doi": "",
        "extra": [
            ["systematic review", "系统综述"],
            ["ANGELO framework", "ANGELO 框架"],
            ["inclusion criteria", "纳入标准"],
            ["exclusion criteria", "排除标准"],
            ["quality assessment", "质量评价"],
            ["school-level exposure", "学校层面暴露"],
            ["institutional", "制度性"],
            ["physical environment", "物理环境"],
            ["economic environment", "经济环境"],
            ["socio-cultural environment", "社会文化环境"],
            ["policy environment", "政策环境"],
        ],
    },
}


def main():
    root = sys.argv[1]
    for key, cfg in PAPERS.items():
        d = {
            "meta": {
                "source_pdf": "source.pdf",
                "glossary_version": "v1.0",
                "layout": "段落交错 + 表格/插图原图保真 + 参考文献保留原文 + 每页「原文 p.X」锚点",
                "item_key": key,
                "title_zh": cfg["title_zh"],
                "journal": cfg["journal"],
                "doi": cfg["doi"],
                "translator": "DSH 子代理并行分块翻译 + 二遍审校 + 机械校验",
                "doc_kind_label": "中英对照全文",
                "print_glossary": False,
                "qa_notes": [
                    "参考文献按学术惯例保留原文（不译）。",
                    "表格/插图原图保真；续页表题标「（续）」。",
                    "未做句级逐句校对：段落级二遍审校 + 机械校验。",
                ],
            },
            "glossary": BASE + cfg["extra"],
        }
        out = os.path.join(root, key, "work", "glossary_meta.json")
        json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(key, "glossary", len(d["glossary"]))


main()

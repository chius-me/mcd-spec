---
name: mcd-spec
description: 把预算、热量、忌口和饮料偏好编译成一份已经核过价的麦当劳购物车。用户要在这些约束下决定点什么时使用。
---

# mcd-spec

这个技能只编译购物车。不要调用 `create-order`、`cancel-order`、`auto-bind-coupons`、`draw-lottery` 或 `mall-create-order`。即使用户说「下单」，也先给出核价结果，并请用户到麦当劳官方渠道确认。

## 步骤

1. 确认场景。用户没说时用到店 `pickup`。外送是 `delivery`，得来速是 `drive`。
2. 到店和得来速需要城市与地点，或者用户已经给出的 `storeCode` 和 `beCode`。外送有多个地址时，把命令列出的地址编号交给用户选，不要自己挑。
3. 在仓库根目录运行编译器。令牌只放在环境变量 `MCD_MCP_TOKEN`，不要写进文件、参数或回复。

```bash
python3 -m mcd_spec compile \
  --city 上海 \
  --place 人民广场 \
  --budget 40 \
  --max-kcal 700 \
  --avoid 可乐,酸黄瓜 \
  --drink 无糖 \
  --want 鸡腿堡
```

没有令牌、只想看编译逻辑时，加 `--demo`。示例数据不是实时菜单。

4. 向用户转述方案 1 的状态、做了哪些调整、核价和热量。说明这还没有下单。
5. 购物车 JSON 里的金额字段单位是分。终端里的「核价」已经是元，给用户报这个数。

---
name: "amap"
description: "高德地图 Web API 封装：地理编码、逆地理编码、POI搜索、路径规划、天气查询、IP定位"
---

# 高德地图 API 技能

本技能封装高德开放平台 Web API，提供地理编码、POI搜索、路径规划、天气查询、IP定位等功能。

## API Key

Key 存储在 `~/.openclaw/.env` 中变量名为 `AMAP_KEY`，当前值为：
`a72255a84ff4b6eb6d70bf6e47f1edba`

## 基础用法

所有请求都通过 `AMAP_BASE = "https://restapi.amap.com/v3"` 发送，
参数中附带 `key={AMAP_KEY}` 和 `output=JSON`。

### 地理编码/逆地理编码

```bash
# 地理编码：地址 → 经纬度
curl -s "https://restapi.amap.com/v3/geocode/geo?key=AMAP_KEY&address=深圳市南山区科技园&city=深圳"

# 逆地理编码：经纬度 → 地址详情
curl -s "https://restapi.amap.com/v3/geocode/regeo?key=AMAP_KEY&location=116.397428,39.90923&radius=1000&extensions=all"
```

### POI 搜索

```bash
# 关键字搜索
curl -s "https://restapi.amap.com/v3/place/text?key=AMAP_KEY&keywords=咖啡&types=050000&city=深圳&offset=10&page=1"

# 周边搜索（圆形区域）
curl -s "https://restapi.amap.com/v3/place/around?key=AMAP_KEY&keywords=餐厅&location=116.397428,39.90923&radius=1000&offset=10"

# 多边形搜索
curl -s "https://restapi.amap.com/v3/place/polygon?key=AMAP_KEY&polygon=116.35,39.88|116.45,39.96&keywords=学校"
```

### 路径规划

```bash
# 驾车路径规划
curl -s "https://restapi.amap.com/v3/direction/driving?key=AMAP_KEY&origin=116.397428,39.90923&destination=116.495252,39.912183&strategy=0&extensions=base"

# 公交路径规划
curl -s "https://restapi.amap.com/v3/direction/transit/integrated?key=AMAP_KEY&origin=116.397428,39.90923&destination=116.495252,39.912183&city=北京&cityd=北京"

# 骑行路径规划
curl -s "https://restapi.amap.com/v3/direction/bicycling?key=AMAP_KEY&origin=116.397428,39.90923&destination=116.495252,39.912183"

# 步行路径规划
curl -s "https://restapi.amap.com/v3/direction/walking?key=AMAP_KEY&origin=116.397428,39.90923&destination=116.495252,39.912183"
```

### 天气查询

```bash
# 天气预报（3天）
curl -s "https://restapi.amap.com/v3/weather/weatherInfo?key=AMAP_KEY&city=440300&extensions=all"

# 实时天气
curl -s "https://restapi.amap.com/v3/weather/weatherInfo?key=AMAP_KEY&city=440300&extensions=base"
```

### IP 定位

```bash
curl -s "https://restapi.amap.com/v3/ip?key=AMAP_KEY&ip=114.114.114.114"
```

### 行政区划查询

```bash
# 查询省份列表
curl -s "https://restapi.amap.com/v3/config/district?key=AMAP_KEY&keywords=中国&subdistrict=1"

# 查询某个城市下级区划
curl -s "https://restapi.amap.com/v3/config/district?key=AMAP_KEY&keywords=深圳&subdistrict=2"
```

### 驾车距离测量

```bash
curl -s "https://restapi.amap.com/v3/distance?key=AMAP_KEY&type=1&origins=116.397428,39.90923&destination=116.495252,39.912183"
```

## 常用城市编码和 adcode

| 城市 | adcode |
|------|--------|
| 北京 | 110000 |
| 上海 | 310000 |
| 广州 | 440100 |
| 深圳 | 440300 |
| 杭州 | 330100 |
| 成都 | 510100 |
| 武汉 | 420100 |
| 南京 | 320100 |
| 重庆 | 500000 |
| 全国 | 100000 |

## 注意事项

- 单日调用限额请参考高德开放平台账号配额（个人开发者通常是 3000-50000 次/日）
- 高德 API 返回格式统一为 JSON，`status=1` 表示成功
- 请求频率不要超过 30 次/秒（QPS 限制 30）
- IP 定位不需要传 `output=JSON`，默认返回 JSON
- POI 类型码参考：[高德 POI 分类表](https://lbs.amap.com/api/webservice/download)

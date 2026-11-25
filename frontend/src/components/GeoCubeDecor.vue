<template>
  <!--
    原创 3D 等轴测立体几何块簇（Isometric Cubes）
    全部由内联 SVG 多边形绘制，无任何外部图片 / 官方会徽 / 摄影素材。
    颜色：深蓝 #08335E、湖蓝 #0077B6、青绿 #1CA7B8、琥珀金 #F5A623（仅高光面，面积 ≤5%）
  -->
  <svg
    class="geo-cube"
    :width="width"
    :height="height"
    :viewBox="`0 0 ${VB_W} ${VB_H}`"
    xmlns="http://www.w3.org/2000/svg"
    role="img"
    :aria-label="ariaLabel"
  >
    <g v-for="(cube, index) in cubes" :key="index" :opacity="cube.opacity">
      <!-- 顶面 -->
      <polygon :points="cube.top" :fill="cube.topFill" />
      <!-- 左面 -->
      <polygon :points="cube.left" :fill="cube.leftFill" />
      <!-- 右面 -->
      <polygon :points="cube.right" :fill="cube.rightFill" />
      <!-- 顶面高光细线 -->
      <polyline :points="cube.highlight" fill="none" stroke="#ffffff" stroke-opacity="0.18" stroke-width="1.2" />
    </g>
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** hero: 大块簇 / footer: 小块簇 / login: 超大块簇 */
    variant?: 'hero' | 'footer' | 'login'
    width?: number | string
    height?: number | string
    ariaLabel?: string
  }>(),
  { variant: 'hero', width: 300, height: 200, ariaLabel: '原创等轴测立体几何装饰' },
)

const VB_W = 360
const VB_H = 240

type CubeSpec = {
  cx: number
  cy: number
  size: number
  depth: number
  topFill: string
  leftFill: string
  rightFill: string
  opacity: number
}

/** 手工排布的等轴测块簇：底座 3 块 + 中层 2 块 + 顶层 1 块 + 1 块琥珀高光点缀 */
const layout: Record<string, CubeSpec[]> = {
  hero: [
    { cx: 92, cy: 158, size: 52, depth: 30, topFill: '#1ca7b8', leftFill: '#08335e', rightFill: '#0077b6', opacity: 1 },
    { cx: 178, cy: 158, size: 52, depth: 30, topFill: '#0077b6', leftFill: '#062b56', rightFill: '#0a3d73', opacity: 1 },
    { cx: 264, cy: 158, size: 52, depth: 30, topFill: '#22b8cf', leftFill: '#08335e', rightFill: '#0077b6', opacity: 1 },
    { cx: 135, cy: 104, size: 52, depth: 30, topFill: '#22b8cf', leftFill: '#062b56', rightFill: '#1ca7b8', opacity: 1 },
    { cx: 221, cy: 104, size: 52, depth: 30, topFill: '#f5a623', leftFill: '#0a3d73', rightFill: '#f8b62d', opacity: 1 },
    { cx: 178, cy: 50, size: 52, depth: 30, topFill: '#22b8cf', leftFill: '#08335e', rightFill: '#0077b6', opacity: 1 },
  ],
  footer: [
    { cx: 92, cy: 158, size: 52, depth: 30, topFill: '#1ca7b8', leftFill: '#08335e', rightFill: '#0077b6', opacity: 0.95 },
    { cx: 178, cy: 158, size: 52, depth: 30, topFill: '#0077b6', leftFill: '#062b56', rightFill: '#0a3d73', opacity: 0.95 },
    { cx: 135, cy: 104, size: 52, depth: 30, topFill: '#f5a623', leftFill: '#0a3d73', rightFill: '#f8b62d', opacity: 0.95 },
    { cx: 221, cy: 104, size: 52, depth: 30, topFill: '#22b8cf', leftFill: '#08335e', rightFill: '#0077b6', opacity: 0.95 },
  ],
  login: [
    { cx: 92, cy: 158, size: 52, depth: 30, topFill: '#1ca7b8', leftFill: '#08335e', rightFill: '#0077b6', opacity: 1 },
    { cx: 178, cy: 158, size: 52, depth: 30, topFill: '#0077b6', leftFill: '#062b56', rightFill: '#0a3d73', opacity: 1 },
    { cx: 264, cy: 158, size: 52, depth: 30, topFill: '#22b8cf', leftFill: '#08335e', rightFill: '#0077b6', opacity: 1 },
    { cx: 135, cy: 104, size: 52, depth: 30, topFill: '#22b8cf', leftFill: '#062b56', rightFill: '#1ca7b8', opacity: 1 },
    { cx: 221, cy: 104, size: 52, depth: 30, topFill: '#f5a623', leftFill: '#0a3d73', rightFill: '#f8b62d', opacity: 1 },
    { cx: 178, cy: 50, size: 52, depth: 30, topFill: '#22b8cf', leftFill: '#08335e', rightFill: '#0077b6', opacity: 1 },
    { cx: 306, cy: 104, size: 52, depth: 30, topFill: '#0077b6', leftFill: '#062b56', rightFill: '#1ca7b8', opacity: 0.75 },
  ],
}

function polygon(points: Array<[number, number]>): string {
  return points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
}

const cubes = computed(() =>
  (layout[props.variant] ?? layout.hero).map((spec) => {
    const { cx, cy, size: s, depth: d } = spec
    const top: Array<[number, number]> = [
      [cx, cy - s / 2],
      [cx + s, cy],
      [cx, cy + s / 2],
      [cx - s, cy],
    ]
    const left: Array<[number, number]> = [
      [cx - s, cy],
      [cx, cy + s / 2],
      [cx, cy + s / 2 + d],
      [cx - s, cy + d],
    ]
    const right: Array<[number, number]> = [
      [cx, cy + s / 2],
      [cx + s, cy],
      [cx + s, cy + d],
      [cx, cy + s / 2 + d],
    ]
    return {
      top: polygon(top),
      left: polygon(left),
      right: polygon(right),
      highlight: polygon([top[3], top[0], top[1]]),
      topFill: spec.topFill,
      leftFill: spec.leftFill,
      rightFill: spec.rightFill,
      opacity: spec.opacity,
    }
  }),
)
</script>

<style scoped>
.geo-cube {
  display: block;
  pointer-events: none;
  filter: drop-shadow(0 10px 18px rgba(2, 20, 40, 0.35));
}
</style>

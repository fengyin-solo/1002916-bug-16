<template>
  <section class="page" data-module="pipeline">
    <header class="page-head">
      <div>
        <h2>压力管道管理</h2>
        <p class="page-desc">维护压力管道，围绕管道编号、管道级别、设计压力、输送介质做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记压力管道</button>
        <button class="btn" type="button" @click="exportRows">导出压力管道清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="search">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>管道状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div v-if="listError" class="retry-bar">
      <span class="error-text">{{ listError }}</span>
      <button class="btn primary" type="button" @click="reload">重试</button>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
          <th>明细</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ display(row, column) }}</td>
          <td class="row-actions">
            <template v-if="allowedActions(row.status).length">
              <button
                v-for="action in allowedActions(row.status)"
                :key="action.name"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action.name }}
              </button>
            </template>
            <span v-else class="muted-text">已报废，终态不可操作</span>
          </td>
          <td>
            <button class="link" type="button" @click="openDetail(row)">查看详情</button>
          </td>
        </tr>
        <tr v-if="!rows.length && !listError">
          <td :colspan="columns.length + 2" class="empty-state">暂无压力管道数据，可先登记压力管道</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条压力管道记录，第 {{ page }} / {{ totalPages }} 页</span>
      <span class="pager">
        <button class="btn" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <button class="btn" type="button" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
        <select v-model.number="size" @change="goPage(1)">
          <option :value="10">每页 10 条</option>
          <option :value="20">每页 20 条</option>
          <option :value="50">每页 50 条</option>
        </select>
      </span>
      <span v-if="actionError" class="error-text">{{ actionError }}</span>
    </footer>

    <div v-if="detail" class="detail-mask" @click.self="closeDetail">
      <div class="detail-panel">
        <header class="detail-head">
          <h3>压力管道详情 · {{ detail['管道编号'] }}</h3>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </header>
        <dl v-if="!detailError" class="detail-grid">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ display(detail, field) }}</dd>
          </template>
          <dt>当前状态</dt>
          <dd><strong>{{ detail.status }}</strong></dd>
        </dl>
        <div v-else class="retry-bar">
          <span class="error-text">{{ detailError }}</span>
          <button class="btn primary" type="button" @click="loadDetail(Number(detail.id))">重试</button>
        </div>
        <footer class="detail-actions">
          <button
            v-for="action in allowedActions(detail.status)"
            :key="action.name"
            class="btn"
            type="button"
            @click="runAction(action, detail)"
          >
            {{ action.name }}
          </button>
          <span v-if="!allowedActions(detail.status).length" class="muted-text">已报废，终态不可操作</span>
        </footer>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

interface ActionDef {
  name: string
  payload: Record<string, string>
}

const ENDPOINT = '/api/pipeline'
const columns = ["管道编号", "管道级别", "设计压力", "输送介质", "管道长度", "敷设方式", "检验日期", "管道状态"]
const detailFields = columns.slice(0, 7)
const statuses = ["正常", "壁厚减薄", "待检验", "已报废"]

// 状态只能逐级向下：每一档只暴露紧邻的下一步；待检验额外可按检验结论定格；已报废无动作
const ACTIONS_BY_STATUS: Record<string, ActionDef[]> = {
  "正常": [{ name: "记录减薄", payload: { action: "记录减薄" } }],
  "壁厚减薄": [{ name: "安排检验", payload: { action: "安排检验" } }],
  "待检验": [
    { name: "申请报废", payload: { action: "申请报废" } },
    { name: "检验合格", payload: { action: "录入检验结论", 结论: "合格" } },
    { name: "检验减薄", payload: { action: "录入检验结论", 结论: "壁厚减薄" } },
    { name: "检验报废", payload: { action: "录入检验结论", 结论: "报废" } },
  ],
  "已报废": [],
}

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(10)
const listError = ref('')
const actionError = ref('')
const filters = ref<Record<string, string>>({})
const statusFilter = ref('')
const filterFields = columns.slice(0, 3)

const detail = ref<Row | null>(null)
const detailError = ref('')

const stats = ref([
  { label: "正常管道", value: 0 },
  { label: "减薄管道", value: 0 },
  { label: "待检验管道", value: 0 },
  { label: "已报废管道", value: 0 },
])

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size.value)))

function allowedActions(status: unknown): ActionDef[] {
  return ACTIONS_BY_STATUS[String(status)] ?? []
}

// 管道状态列直接以 status 为准，列表和详情共用同一份行数据，两处口径一致
function display(row: Row, column: string): string | number | null {
  if (column === "管道状态") return String(row.status ?? "")
  const value = row[column]
  return value === null || value === undefined || value === "" ? "—" : (value as string | number)
}

function buildQuery() {
  const params = new URLSearchParams()
  for (const field of filterFields) {
    const value = filters.value[field]?.trim()
    // 后端只支持按管道编号检索；其余字段不参与过滤，避免把无效键发出去导致口径不一致
    if (field === "管道编号" && value) {
      params.set("keyword", value)
    }
  }
  if (statusFilter.value) {
    params.set("status", statusFilter.value)
  }
  params.set("page", String(page.value))
  params.set("size", String(size.value))
  return params.toString()
}

async function readErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string; message?: string }
    return payload.message || payload.detail || fallback
  } catch {
    return fallback
  }
}

async function reload() {
  listError.value = ''
  try {
    const response = await request(`${ENDPOINT}?${buildQuery()}`)
    if (!response.ok) {
      throw new Error(await readErrorMessage(response, '压力管道列表读取失败'))
    }
    const payload = (await response.json()) as { items?: Row[]; total?: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    rows.value = []
    total.value = 0
    listError.value = error instanceof Error ? error.message : '压力管道列表读取失败'
  }
  void refreshStats()
}

async function refreshStats() {
  try {
    const response = await request(`${ENDPOINT}/export`)
    if (!response.ok) {
      return
    }
    const payload = (await response.json()) as { items?: Row[] }
    const items = payload.items ?? []
    stats.value = [
      { label: "正常管道", value: items.filter((row) => row.status === "正常").length },
      { label: "减薄管道", value: items.filter((row) => row.status === "壁厚减薄").length },
      { label: "待检验管道", value: items.filter((row) => row.status === "待检验").length },
      { label: "已报废管道", value: items.filter((row) => row.status === "已报废").length },
    ]
  } catch {
    // 统计卡片不阻塞列表，列表自身的错误条负责重试
  }
}

function search() {
  return goPage(1)
}

function resetFilters() {
  filters.value = {}
  statusFilter.value = ''
  void goPage(1)
}

async function goPage(target: number) {
  page.value = Math.min(Math.max(target, 1), totalPages.value || 1)
  await reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  actionError.value = '压力管道登记入口尚未接入审批流'
}

async function loadDetail(entryId: number) {
  detailError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${entryId}`)
    if (!response.ok) {
      throw new Error(await readErrorMessage(response, '压力管道详情读取失败'))
    }
    detail.value = (await response.json()) as Row
    // 详情刷新后同步列表里的同一行，保证两处读到同一份最新结果
    const index = rows.value.findIndex((row) => String(row.id) === String(entryId))
    if (index >= 0 && detail.value) {
      rows.value[index] = detail.value
    }
  } catch (error) {
    if (!detail.value || String(detail.value.id) !== String(entryId)) {
      detail.value = { id: entryId }
    }
    detailError.value = error instanceof Error ? error.message : '压力管道详情读取失败'
  }
}

function openDetail(row: Row) {
  void loadDetail(Number(row.id))
}

function closeDetail() {
  detail.value = null
  detailError.value = ''
}

async function runAction(action: ActionDef, row: Row) {
  actionError.value = ''
  const entryId = Number(row.id)
  try {
    const response = await request(`${ENDPOINT}/${entryId}/actions`, {
      method: 'POST',
      body: JSON.stringify(action.payload),
    })
    const payload = (await response.json().catch(() => null)) as { ok?: boolean; message?: string; entry?: Row } | null
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '压力管道动作未生效，请稍后重试')
    }
    // 以服务端返回的最新状态为准，列表与详情同时换出新结果
    if (payload.entry) {
      const index = rows.value.findIndex((item) => String(item.id) === String(entryId))
      if (index >= 0) {
        rows.value[index] = payload.entry
      }
      if (detail.value && String(detail.value.id) === String(entryId)) {
        detail.value = payload.entry
      }
    }
    await reload()
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '压力管道操作失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.muted-text { color: var(--muted); font-size: 12px; }
.retry-bar { display: flex; align-items: center; gap: 12px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; }
.pager { display: inline-flex; align-items: center; gap: 8px; }
.pager .btn:disabled { opacity: 0.5; cursor: not-allowed; }
.detail-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); display: flex; justify-content: flex-end; z-index: 20; }
.detail-panel { width: 420px; max-width: 90vw; background: #fff; min-height: 100vh; padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; }
.detail-head { display: flex; justify-content: space-between; align-items: center; }
.detail-head h3 { margin: 0; font-size: 16px; }
.detail-grid { display: grid; grid-template-columns: 96px 1fr; gap: 8px 12px; margin: 0; font-size: 13px; }
.detail-grid dt { color: var(--muted); }
.detail-grid dd { margin: 0; }
.detail-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: auto; padding-top: 12px; border-top: 1px solid var(--border); }
</style>

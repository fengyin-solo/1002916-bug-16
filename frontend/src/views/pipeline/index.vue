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
      <article
        v-for="item in statCards"
        :key="item.label"
        class="stat-card"
        :class="{ active: filters.status === item.status }"
        style="cursor: pointer"
        @click="toggleStatusFilter(item.status)"
      >
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="search">
      <label class="filter-item">
        <span>管道编号</span>
        <input v-model="filters.keyword" placeholder="按管道编号检索" />
      </label>
      <label class="filter-item">
        <span>管道状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <button v-if="column === '管道编号'" class="link" type="button" @click="openDetail(Number(row.id))">
              {{ row[column] ?? '—' }}
            </button>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in allowedActions(row.status as string)"
              :key="action"
              class="link"
              type="button"
              @click="handleAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!allowedActions(row.status as string).length" class="muted-text">—</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">
            {{ loadError ? '列表读取失败' : '暂无压力管道数据，可先登记压力管道' }}
          </td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条压力管道记录 · 第 {{ page }} / {{ totalPages }} 页</span>
      <span class="pager">
        <button class="btn" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <button class="btn" type="button" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
      </span>
      <span v-if="actionMessage" :class="actionOk ? 'ok-text' : 'error-text'">
        {{ actionMessage }}
      </span>
      <span v-if="loadError" class="error-text">
        {{ loadError }}
        <button class="link" type="button" @click="reload">重试</button>
      </span>
    </footer>

    <!-- 检验结论录入：检验完成后按结论把待检验管道重新定格 -->
    <div v-if="conclusionTarget" class="modal-mask" @click.self="conclusionTarget = null">
      <div class="modal-card">
        <h3>录入检验结论 · {{ conclusionTarget['管道编号'] }}</h3>
        <p class="page-desc">管道当前为「待检验」，提交后按结论定格：合格→正常，壁厚减薄→壁厚减薄，报废→已报废。</p>
        <div class="conclusion-options">
          <button
            v-for="option in conclusionOptions"
            :key="option"
            class="btn"
            type="button"
            :disabled="submitting"
            @click="submitConclusion(option)"
          >
            结论：{{ option }}
          </button>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" :disabled="submitting" @click="conclusionTarget = null">取消</button>
        </div>
      </div>
    </div>

    <!-- 明细：详情页与列表页走同一接口口径，级别、设计压力、状态不会再各说各话 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal-card">
        <h3>管道明细 · {{ detail['管道编号'] }}</h3>
        <table class="data-table">
          <tbody>
            <tr v-for="column in detailColumns" :key="column">
              <th>{{ column }}</th>
              <td>{{ detail[column] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button
            v-for="action in allowedActions(detail.status as string)"
            :key="action"
            class="btn"
            type="button"
            @click="handleAction(action, detail)"
          >
            {{ action }}
          </button>
          <button class="btn ghost" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/pipeline'
const PAGE_SIZE = 10
const columns = ["管道编号", "管道级别", "设计压力", "输送介质", "管道长度", "敷设方式", "检验日期", "管道状态"]
const detailColumns = columns
const statuses = ["正常", "壁厚减薄", "待检验", "已报废"]

// 状态只能逐级往下走：每档只开放“下一档”动作；待检验的管道改为按检验结论定格
const NEXT_ACTION: Record<string, string[]> = {
  '正常': ['记录减薄'],
  '壁厚减薄': ['安排检验'],
  '待检验': ['录入结论', '申请报废'],
  '已报废': [],
}
const conclusionOptions = ['合格', '壁厚减薄', '报废']

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const totalPages = computed(() => Math.max(Math.ceil(total.value / PAGE_SIZE), 1))
const loadError = ref('')
const actionMessage = ref('')
const actionOk = ref(true)
const submitting = ref(false)
const filters = reactive<{ keyword: string; status: string }>({ keyword: '', status: '' })

const counts = ref<Record<string, number>>({ 正常: 0, 壁厚减薄: 0, 待检验: 0, 已报废: 0 })
const statCards = computed(() => [
  { label: '正常管道', status: '正常', value: counts.value['正常'] ?? 0 },
  { label: '减薄管道', status: '壁厚减薄', value: counts.value['壁厚减薄'] ?? 0 },
  { label: '待检验管道', status: '待检验', value: counts.value['待检验'] ?? 0 },
  { label: '已报废管道', status: '已报废', value: counts.value['已报废'] ?? 0 },
])

const conclusionTarget = ref<Row | null>(null)
const detail = ref<Row | null>(null)

function allowedActions(status: string): string[] {
  return NEXT_ACTION[status] ?? []
}

function buildQuery() {
  const params = new URLSearchParams()
  if (filters.keyword.trim()) params.set('keyword', filters.keyword.trim())
  if (filters.status) params.set('status', filters.status)
  params.set('page', String(page.value))
  params.set('size', String(PAGE_SIZE))
  return params.toString()
}

async function reload() {
  loadError.value = ''
  try {
    const response = await request(`${ENDPOINT}?${buildQuery()}`)
    if (!response.ok) {
      throw new Error('压力管道列表读取失败')
    }
    const payload = await response.json()
    // 越界页后端会夹回有效页，以前端收到的 page 为准，避免翻到空页
    page.value = payload.page ?? page.value
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    // 失败时保留旧页码并给出重试入口，不清空当前页内容之外的信息
    loadError.value = error instanceof Error ? error.message : '压力管道列表读取失败'
  }
}

async function loadCounts() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (response.ok) {
      counts.value = await response.json()
    }
  } catch {
    // 统计卡加载失败不阻塞列表
  }
}

function search() {
  page.value = 1
  void reload()
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  page.value = 1
  void reload()
}

function toggleStatusFilter(status: string) {
  filters.status = filters.status === status ? '' : status
  search()
}

function goPage(target: number) {
  if (target < 1 || target > totalPages.value) return
  page.value = target
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  reportAction(false, '压力管道登记入口尚未接入审批流')
}

function reportAction(ok: boolean, message: string) {
  actionOk.value = ok
  actionMessage.value = message
}

async function postAction(url: string, body: Record<string, unknown>) {
  try {
    const response = await request(url, { method: 'POST', body: JSON.stringify(body) })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload) {
      return { ok: false, message: '压力管道动作未生效，请稍后重试' }
    }
    return payload as { ok: boolean; message: string; entry?: Row }
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : '压力管道操作失败' }
  }
}

async function syncAfterAction() {
  await Promise.all([reload(), loadCounts()])
}

function handleAction(action: string, row: Row) {
  actionMessage.value = ''
  if (action === '录入结论') {
    conclusionTarget.value = row
    return
  }
  void runTransition(action, row)
}

async function runTransition(action: string, row: Row) {
  if (submitting.value) return
  submitting.value = true
  try {
    const result = await postAction(`${ENDPOINT}/${row.id}/actions`, { values: { action } })
    reportAction(result.ok, result.message)
    if (!result.ok) return
    // 管道当前状态以最后一次操作为准：动作成功后两处同时换新结果
    if (detail.value && detail.value.id === row.id && result.entry) {
      detail.value = result.entry
    }
    await syncAfterAction()
  } finally {
    submitting.value = false
  }
}

async function submitConclusion(conclusion: string) {
  const target = conclusionTarget.value
  if (!target || submitting.value) return
  submitting.value = true
  try {
    const result = await postAction(`${ENDPOINT}/${target.id}/conclusion`, {
      values: { conclusion },
    })
    reportAction(result.ok, result.message)
    if (result.ok) {
      conclusionTarget.value = null
      await syncAfterAction()
    }
  } finally {
    submitting.value = false
  }
}

async function openDetail(id: number) {
  try {
    const response = await request(`${ENDPOINT}/${id}`)
    if (!response.ok) {
      reportAction(false, '管道明细读取失败')
      return
    }
    detail.value = await response.json()
  } catch (error) {
    reportAction(false, error instanceof Error ? error.message : '管道明细读取失败')
  }
}

onMounted(() => {
  void reload()
  void loadCounts()
})
</script>

<style scoped>
.muted-text { color: var(--muted); }
.ok-text { color: #067647; }
.stat-card.active { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand); }
.pager { display: flex; gap: 6px; }
.pager .btn[disabled] { opacity: 0.5; cursor: not-allowed; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal-card {
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  width: 560px;
  max-width: calc(100vw - 40px);
  max-height: 80vh;
  overflow: auto;
}
.modal-card h3 { margin: 0 0 10px; font-size: 16px; }
.conclusion-options { display: flex; gap: 10px; margin: 14px 0; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
</style>

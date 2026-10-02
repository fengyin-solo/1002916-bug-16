<template>
  <section class="page" data-module="inspection">
    <header class="page-head">
      <div>
        <h2>定期检验管理</h2>
        <p class="page-desc">维护检验任务，围绕检验编号、被检设备、检验类别、检验机构做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记检验任务</button>
        <button class="btn" type="button" @click="exportRows">导出定期检验清单</button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="search">
      <label class="filter-item">
        <span>检验编号</span>
        <input v-model="filters.keyword" placeholder="按检验编号检索" />
      </label>
      <label class="filter-item">
        <span>检验状态</span>
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
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
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
            {{ loadError ? '清单读取失败' : '暂无定期检验数据，可先登记检验任务' }}
          </td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条定期检验记录 · 第 {{ page }} / {{ totalPages }} 页</span>
      <span class="pager">
        <button class="btn" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <button class="btn" type="button" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
      </span>
      <span v-if="actionMessage" :class="actionOk ? 'ok-text' : 'error-text'">{{ actionMessage }}</span>
      <span v-if="loadError" class="error-text">
        {{ loadError }}
        <button class="link" type="button" @click="reload">重试</button>
      </span>
    </footer>

    <!-- 录入结论：结论文本决定任务合格/不合格，关联压力管道的任务同时让管道按结论定格 -->
    <div v-if="conclusionTarget" class="modal-mask" @click.self="conclusionTarget = null">
      <div class="modal-card">
        <h3>录入检验结论 · {{ conclusionTarget['检验编号'] }}</h3>
        <label class="filter-item">
          <span>检验结论</span>
          <textarea v-model="conclusionText" rows="3" placeholder="如：合格继续使用 / 壁厚减薄需整改 / 报废"></textarea>
        </label>
        <div class="quick-options">
          <button v-for="option in quickConclusions" :key="option" class="btn ghost" type="button" @click="conclusionText = option">
            {{ option }}
          </button>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" :disabled="submitting" @click="conclusionTarget = null">取消</button>
          <button class="btn primary" type="button" :disabled="submitting || !conclusionText.trim()" @click="submitConclusion">
            提交结论
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/inspection'
const PAGE_SIZE = 10
const columns = ["检验编号", "被检设备", "检验类别", "检验机构", "计划检验日", "实际检验日", "检验结论", "检验状态"]
const statuses = ["待检验", "检验中", "合格", "不合格", "已取消"]

// 检验任务同样逐级走：待检验 → 检验中 → 合格/不合格；终态（含已取消）不再有动作
const NEXT_ACTIONS: Record<string, string[]> = {
  '待检验': ['安排检验'],
  '检验中': ['录入结论', '下达整改'],
  '合格': [],
  '不合格': [],
  '已取消': [],
}
const quickConclusions = ['合格，继续使用', '壁厚减薄，下达整改', '报废']

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const totalPages = computed(() => Math.max(Math.ceil(total.value / PAGE_SIZE), 1))
const loadError = ref('')
const actionMessage = ref('')
const actionOk = ref(true)
const submitting = ref(false)
const filters = reactive<{ keyword: string; status: string }>({ keyword: '', status: '' })

const conclusionTarget = ref<Row | null>(null)
const conclusionText = ref('')

function allowedActions(status: string): string[] {
  return NEXT_ACTIONS[status] ?? []
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
      throw new Error('检验任务列表读取失败')
    }
    const payload = await response.json()
    page.value = payload.page ?? page.value
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '检验任务列表读取失败'
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

function goPage(target: number) {
  if (target < 1 || target > totalPages.value) return
  page.value = target
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  actionOk.value = false
  actionMessage.value = '检验任务登记入口尚未接入审批流'
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
      return { ok: false, message: '定期检验动作未生效，请稍后重试' }
    }
    return payload as { ok: boolean; message: string }
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : '定期检验操作失败' }
  }
}

async function handleAction(action: string, row: Row) {
  actionMessage.value = ''
  if (action === '录入结论') {
    conclusionTarget.value = row
    conclusionText.value = ''
    return
  }
  if (submitting.value) return
  submitting.value = true
  try {
    const result = await postAction(`${ENDPOINT}/${row.id}/actions`, { values: { action } })
    reportAction(result.ok, result.message)
    if (result.ok) {
      await reload()
    }
  } finally {
    submitting.value = false
  }
}

async function submitConclusion() {
  const target = conclusionTarget.value
  if (!target || submitting.value) return
  submitting.value = true
  try {
    const result = await postAction(`${ENDPOINT}/${target.id}/actions`, {
      values: { action: '录入结论', conclusion: conclusionText.value.trim() },
    })
    reportAction(result.ok, result.message)
    if (result.ok) {
      conclusionTarget.value = null
      await reload()
    }
  } finally {
    submitting.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.muted-text { color: var(--muted); }
.ok-text { color: #067647; }
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
  width: 520px;
  max-width: calc(100vw - 40px);
}
.modal-card h3 { margin: 0 0 10px; font-size: 16px; }
.modal-card textarea { width: 100%; margin-top: 4px; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.quick-options { display: flex; gap: 8px; margin: 10px 0; flex-wrap: wrap; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
</style>

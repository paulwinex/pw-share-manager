<template>
  <q-page class="q-px-md">
<!--    <div class="text-h5 q-mb-md">Shares</div>-->

    <div class="row items-center justify-between q-mb-md">
      <q-input
        v-model="nameFilter"
        label="Filter by name"
        clearable
        dense
        debounce="200"
        style="max-width: 300px"
      />
      <q-btn flat round icon="add" color="primary" title="Create share" @click="openCreate" />
    </div>

    <q-table
      :rows="filteredShares"
      :columns="columns"
      row-key="id"
      :loading="loading"
      :pagination="{ rowsPerPage: 50 }"
      :rows-per-page-options="[10, 25, 50, 100, 0]"
      flat
      bordered
    >
      <template v-slot:body-cell-actions="cell">
        <q-td :props="cell" class="text-right">
          <q-btn
            flat
            round
            dense
            icon="edit"
            color="grey-7"
            title="Edit"
            @click="openEdit(cell.row)"
          />
          <q-btn
            flat
            round
            dense
            icon="delete"
            color="negative"
            title="Delete"
            @click="confirmDelete(cell.row)"
          />
        </q-td>
      </template>
      <template v-slot:body-cell-wide_links="cell">
        <q-td :props="cell">
          <q-icon
            v-if="cell.row.wide_links"
            name="link"
            color="warning"
            size="18px"
          >
            <q-tooltip>Wide symlinks enabled</q-tooltip>
          </q-icon>
          <span v-else class="text-grey">—</span>
        </q-td>
      </template>
      <template v-slot:no-data>
        <span class="text-grey">No shares</span>
      </template>
    </q-table>

    <q-dialog v-model="dialogOpen">
      <q-card style="min-width: 420px">
        <q-card-section>
          <div class="text-subtitle1">{{ editing ? 'Edit share' : 'Create share' }}</div>
        </q-card-section>
        <q-card-section class="q-gutter-md">
          <q-input v-model="form.name" label="Name" hint="lowercase letters, digits, - _" />
          <q-select
            v-model="form.path"
            :options="pathOptions"
            use-input
            hide-selected
            fill-input
            input-debounce="0"
            @filter="filterPath"
            @input-value="(val) => (form.path = val)"
            label="Path (absolute path on the server)"
            hint="Type any path or choose from server presets"
          >
            <template v-slot:no-option>
              <q-item>
                <q-item-section class="text-grey"> No results </q-item-section>
              </q-item>
            </template>
          </q-select>
          <q-input v-model="form.comment" label="Comment" type="textarea" />
          <q-toggle
            v-model="form.wide_links"
            label="Wide symlinks"
            color="primary"
          />
          <div v-if="form.wide_links" class="text-caption text-negative">
            Symlinks inside this share will be followed even when they point
            outside it. Anyone with access to this share can then read whatever
            the Samba service user can read elsewhere on the server.
          </div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat label="Cancel" v-close-popup />
          <q-btn
            :label="editing ? 'Save' : 'Create'"
            color="primary"
            :loading="saving"
            :disable="!namePattern.test(form.name)"
            @click="save"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>

    <q-dialog v-model="deleteOpen">
      <q-card>
        <q-card-section class="text-h6">Delete {{ deleteTarget?.name }}?</q-card-section>
        <q-card-section class="text-grey">
          The share directory is kept on disk; it is removed from the Samba registry.
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat label="Cancel" v-close-popup @click="deleteOpen = false" />
          <q-btn label="Delete" color="negative" :loading="deleting" @click="doDelete" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useQuasar } from 'quasar';
import type { QTableColumn } from 'quasar';
import { api } from '@/api';
import type { ShareOut } from '@/api/types';

const $q = useQuasar();
const shares = ref<ShareOut[]>([]);
const dirs = ref<string[]>([]);
const pathOptions = ref<string[]>([]);
const loading = ref(false);
const nameFilter = ref('');

const filteredShares = computed(() => {
  const needle = nameFilter.value.trim().toLocaleLowerCase();
  if (!needle) return shares.value;
  return shares.value.filter((s) => s.name.toLocaleLowerCase().includes(needle));
});

const dialogOpen = ref(false);
const editing = ref(false);
const editId = ref('');
const emptyForm = () => ({ name: '', path: '', comment: '', wide_links: false });
const form = ref(emptyForm());
const saving = ref(false);

const deleteOpen = ref(false);
const deleteTarget = ref<ShareOut | null>(null);
const deleting = ref(false);

const columns = computed<QTableColumn[]>(() => {
  const cols: QTableColumn[] = [
    { name: 'name', label: 'Name', field: 'name', align: 'left' },
    { name: 'path', label: 'Path', field: 'path', align: 'left' },
  ];
  if (!$q.screen.lt.md) {
    cols.push({ name: 'comment', label: 'Comment', field: 'comment', align: 'left' });
  }
  cols.push({
    name: 'wide_links',
    label: 'Symlinks',
    field: 'wide_links',
    align: 'left',
  });
  cols.push({ name: 'actions', label: '', field: '', align: 'right' });
  return cols;
});

const namePattern = /^[a-z][a-z0-9_-]{1,31}$/;

async function load() {
  loading.value = true;
  try {
    shares.value = (await api.listShares()).data;
  } catch (e: any) {
    $q.notify({ type: 'negative', message: e?.response?.data?.detail ?? 'Failed to load shares' });
  } finally {
    loading.value = false;
  }
}

async function loadDirs() {
  try {
    dirs.value = (await api.availableDirs()).data;
    pathOptions.value = [...dirs.value];
  } catch (e: any) {
    $q.notify({ type: 'negative', message: e?.response?.data?.detail ?? 'Failed to load available paths' });
  }
}

function openCreate() {
  editing.value = false;
  editId.value = '';
  form.value = emptyForm();
  dialogOpen.value = true;
}

function openEdit(share: ShareOut) {
  editing.value = true;
  editId.value = share.id;
  form.value = {
    name: share.name,
    path: share.path,
    comment: share.comment ?? '',
    wide_links: share.wide_links,
  };
  dialogOpen.value = true;
}

function filterPath(val: string, update: (fn: () => void) => void) {
  update(() => {
    const needle = val.toLocaleLowerCase();
    pathOptions.value = dirs.value.filter((v) => v.toLocaleLowerCase().includes(needle));
  });
}

async function save() {
  saving.value = true;
  try {
    const payload = {
      name: form.value.name,
      path: form.value.path,
      comment: form.value.comment,
      wide_links: form.value.wide_links,
    };
    if (editing.value) {
      await api.updateShare(editId.value, payload);
      $q.notify({ type: 'positive', message: 'Share updated' });
    } else {
      await api.createShare(payload);
      $q.notify({ type: 'positive', message: 'Share created' });
    }
    dialogOpen.value = false;
  } catch (e: any) {
    $q.notify({ type: 'negative', message: e?.response?.data?.detail ?? 'Failed to save share' });
  } finally {
    saving.value = false;
  }
  await load();
  await loadDirs();
}

function confirmDelete(share: ShareOut) {
  deleteTarget.value = share;
  deleteOpen.value = true;
}

async function doDelete() {
  if (!deleteTarget.value) return;
  deleting.value = true;
  try {
    await api.deleteShare(deleteTarget.value.id);
    deleteOpen.value = false;
    $q.notify({ type: 'positive', message: 'Share deleted' });
  } catch (e: any) {
    $q.notify({ type: 'negative', message: e?.response?.data?.detail ?? 'Failed to delete share' });
  } finally {
    deleting.value = false;
  }
  await load();
}

onMounted(async () => {
  await Promise.all([load(), loadDirs()]);
});
</script>

const panels = {
  domains: document.getElementById("panel-domains"),
  proxies: document.getElementById("panel-proxies"),
  groups: document.getElementById("panel-groups"),
  system: document.getElementById("panel-system"),
};

const tabButtons = {
  domains: document.getElementById("tab-domains"),
  proxies: document.getElementById("tab-proxies"),
  groups: document.getElementById("tab-groups"),
  system: document.getElementById("tab-system"),
};

function parseIntVar(value) {
  const num = Number.parseInt(value, 10);
  return Number.isNaN(num) ? 0 : num;
}

function showTab(name) {
  const safeName = Object.prototype.hasOwnProperty.call(panels, name) ? name : "domains";
  Object.entries(panels).forEach(([key, panel]) => {
    if (!panel) return;
    const active = key === safeName;
    panel.style.display = active ? "block" : "none";
    if (tabButtons[key]) {
      tabButtons[key].classList.toggle("active", active);
    }
  });

  const u = new URL(window.location.href);
  u.searchParams.set("tab", safeName);
  history.replaceState({}, "", u.toString());
}

function syncRuleFormGroupInput(activeGroupId) {
  const groupInput = document.getElementById("group-id");
  if (!groupInput || !groupInput.options.length) {
    return;
  }

  if (activeGroupId > 0) {
    groupInput.value = String(activeGroupId);
    return;
  }

  groupInput.selectedIndex = 0;
}

function refreshMode(selectEl) {
  const root = selectEl.dataset.modeRoot;
  const mode = selectEl.value;
  const form = selectEl.closest("form");
  if (!form || !root) {
    return;
  }

  form.querySelectorAll(`[data-form="${root}"]`).forEach((node) => {
    node.classList.toggle("d-none", node.dataset.mode !== mode);
  });
}

function quickFillExact() {
  const input = document.getElementById("rule-pattern");
  if (!input.value.trim()) {
    input.value = "api.example.com";
  }
  document.getElementById("rule-kind").value = "exact";
}

function quickFillSuffix() {
  const input = document.getElementById("rule-pattern");
  const v = input.value.trim() || "example.com";
  input.value = v.startsWith("*.") ? v : `*.${v.replace(/^\\./, "")}`;
  document.getElementById("rule-kind").value = "suffix";
}

function quickFillKeyword() {
  const input = document.getElementById("rule-pattern");
  if (!input.value.trim()) {
    input.value = "paypal";
  }
  document.getElementById("rule-kind").value = "keyword";
}

function getCurrentRuleGroup() {
  const filter = document.getElementById("rule-group-filter");
  return parseIntVar(filter ? filter.value : 0);
}

function setGroupTreeActive(groupId) {
  const nodes = document.querySelectorAll(".group-tree-node");
  nodes.forEach((node) => {
    node.classList.toggle("active", parseIntVar(node.dataset.groupId) === groupId);
  });
}

function updateRuleCountSummary(visibleCount, selectedCount) {
  const visibleEl = document.getElementById("rule-visible-count");
  const selectedEl = document.getElementById("rule-selected-count");
  if (visibleEl) {
    visibleEl.textContent = String(visibleCount);
  }
  if (selectedEl) {
    selectedEl.textContent = String(selectedCount);
  }
}

function filterRulesByGroup(groupId) {
  const targetGroup = parseIntVar(groupId);
  const rows = Array.from(document.querySelectorAll(".rule-row"));
  const selectAll = document.getElementById("rule-select-all");
  const emptyRow = document.querySelector(".rule-empty-row");
  const emptyMatch = document.getElementById("rule-empty-match");

  let visibleCount = 0;
  rows.forEach((row) => {
    const rowGroup = parseIntVar(row.dataset.ruleGroup);
    const visible = targetGroup <= 0 || rowGroup === targetGroup;
    row.classList.toggle("d-none", !visible);
    if (visible) {
      visibleCount += 1;
    }
  });

  if (rows.length === 0 && emptyRow) {
    if (emptyMatch) {
      emptyMatch.classList.add("d-none");
    }
  } else if (emptyRow) {
    emptyRow.classList.add("d-none");
    if (emptyMatch) {
      emptyMatch.classList.toggle("d-none", visibleCount > 0);
    }
  }

  if (selectAll) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
  }

  const selectedCount = document.querySelectorAll(".rule-row-checkbox:checked").length;
  updateRuleCountSummary(visibleCount, selectedCount);

  const filter = document.getElementById("rule-group-filter");
  if (filter) {
    filter.value = String(targetGroup);
  }

  syncRuleGroupLabels(targetGroup);
  setGroupTreeActive(targetGroup);
  syncRuleFormGroupInput(targetGroup);
}

function getTreeLabel(groupId) {
  const node = document.querySelector(`.group-tree-node[data-group-id="${groupId}"]`);
  if (node) {
    return node.dataset.groupName || node.textContent.trim();
  }
  if (groupId > 0) {
    return `分组 ${groupId}`;
  }
  return "全部分组";
}

function syncRuleGroupLabels(groupId) {
  const labelEl = document.getElementById("rule-current-group-label");
  if (labelEl) {
    labelEl.textContent = getTreeLabel(groupId);
  }

  const batchCurrent = document.getElementById("batch-current-group");
  if (batchCurrent) {
    batchCurrent.value = String(groupId);
  }
  const deleteCurrent = document.getElementById("rule-delete-current-group");
  if (deleteCurrent) {
    deleteCurrent.value = String(groupId);
  }
}

function syncSelectAllState() {
  const selectAll = document.getElementById("rule-select-all");
  if (!selectAll) {
    return;
  }

  const visibleChecks = Array.from(document.querySelectorAll(".rule-row"))
    .filter((row) => !row.classList.contains("d-none"))
    .map((row) => row.querySelector(".rule-row-checkbox"))
    .filter(Boolean);

  if (visibleChecks.length === 0) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
    return;
  }

  const checkedCount = visibleChecks.filter((checkbox) => checkbox.checked).length;
  selectAll.checked = checkedCount === visibleChecks.length;
  selectAll.indeterminate = checkedCount > 0 && checkedCount < visibleChecks.length;
}

function updateSelectedSummary() {
  const selectedCount = document.querySelectorAll(".rule-row-checkbox:checked").length;
  const visibleCount = Array.from(document.querySelectorAll(".rule-row")).filter((row) => !row.classList.contains("d-none")).length;
  updateRuleCountSummary(visibleCount, selectedCount);
  syncSelectAllState();
}

function setCurrentRuleGroup(groupId) {
  const targetGroup = parseIntVar(groupId);

  const u = new URL(window.location.href);
  if (targetGroup > 0) {
    u.searchParams.set("rule_group", targetGroup);
  } else {
    u.searchParams.delete("rule_group");
  }
  history.replaceState({}, "", u.toString());

  filterRulesByGroup(targetGroup);
}

function getSelectedRuleIds() {
  return Array.from(document.querySelectorAll(".rule-row-checkbox:checked")).map((checkbox) => checkbox.value);
}

function bindRuleBatchActions() {
  const batchForm = document.getElementById("batch-rule-form");
  const batchDelete = document.getElementById("batch-delete-selected");
  const batchMove = document.getElementById("batch-move-selected");
  const batchTarget = document.getElementById("batch-target-select");
  const batchTargetInput = document.getElementById("batch-target-group");
  if (!batchForm || !batchDelete || !batchMove || !batchTarget || !batchTargetInput) {
    return;
  }

  batchDelete.addEventListener("click", () => {
    const checkedCount = getSelectedRuleIds().length;
    if (checkedCount === 0) {
      alert("请先选择要操作的规则");
      return;
    }
    if (!window.confirm(`确认删除已选中的 ${checkedCount} 条规则？`)) {
      return;
    }
    batchTargetInput.value = "0";
    batchForm.action = `/rules/bulk/delete?current_group=${getCurrentRuleGroup()}`;
    batchForm.submit();
  });

  batchMove.addEventListener("click", () => {
    const checkedCount = getSelectedRuleIds().length;
    if (checkedCount === 0) {
      alert("请先选择要操作的规则");
      return;
    }
    const targetGroup = parseIntVar(batchTarget.value);
    if (targetGroup <= 0) {
      alert("请先选择要移动到的分组");
      return;
    }
    batchTargetInput.value = String(targetGroup);
    batchForm.action = `/rules/bulk/move?target_group=${targetGroup}&current_group=${getCurrentRuleGroup()}`;
    batchForm.submit();
  });
}

function bindRuleSelection() {
  const selectAll = document.getElementById("rule-select-all");
  if (selectAll) {
    selectAll.addEventListener("change", () => {
      const shouldCheck = selectAll.checked;
      document.querySelectorAll(".rule-row")
        .forEach((row) => {
          if (row.classList.contains("d-none")) {
            return;
          }
          const checkbox = row.querySelector(".rule-row-checkbox");
          if (checkbox) {
            checkbox.checked = shouldCheck;
          }
        });
      updateSelectedSummary();
    });
  }

  document.querySelectorAll(".rule-row-checkbox").forEach((checkbox) => {
    checkbox.addEventListener("change", updateSelectedSummary);
  });
}

function bindRuleDeleteButtons() {
  const deleteForm = document.getElementById("rule-delete-form");
  if (!deleteForm) {
    return;
  }

  document.querySelectorAll(".rule-delete-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const ruleId = btn.dataset.ruleId;
      if (!ruleId) {
        return;
      }
      if (!window.confirm("确认删除该规则？")) {
        return;
      }
      deleteForm.action = `/rules/${ruleId}/delete?current_group=${getCurrentRuleGroup()}`;
      deleteForm.submit();
    });
  });
}

function bindRuleTree() {
  const filter = document.getElementById("rule-group-filter");
  const groupNodes = document.querySelectorAll(".group-tree-node");
  groupNodes.forEach((node) => {
    node.addEventListener("click", () => {
      const nextGroup = parseIntVar(node.dataset.groupId);
      setCurrentRuleGroup(nextGroup);
      if (filter) {
        filter.value = String(nextGroup);
      }
    });
  });
}

function bindRuleGroupFilter() {
  const filter = document.getElementById("rule-group-filter");
  if (!filter) {
    return;
  }

  filter.addEventListener("change", () => {
    setCurrentRuleGroup(filter.value);
  });
}

function bindModePanels() {
  document.querySelectorAll(".proxy-mode-select").forEach((selectEl) => {
    refreshMode(selectEl);
    selectEl.addEventListener("change", () => refreshMode(selectEl));
  });
}

function bindTabs(initialTab) {
  Object.keys(tabButtons).forEach((tab) => {
    const btn = tabButtons[tab];
    if (btn) {
      btn.addEventListener("click", () => showTab(tab));
    }
  });
  showTab(initialTab);
}

function bindModePanelsExisting() {
  document.querySelectorAll('[data-form^="proxy-group-"]').forEach((node) => {
    const selectEl = node.closest("form")?.querySelector(".proxy-mode-select[data-mode-root]");
    if (selectEl) {
      refreshMode(selectEl);
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const requestedTab = new URL(window.location.href).searchParams.get("tab");
  const urlTab = requestedTab || window.__DPM_ACTIVE_TAB__ || "domains";
  bindTabs(urlTab);
  bindModePanels();
  bindModePanelsExisting();
  bindRuleGroupFilter();
  bindRuleTree();
  bindRuleSelection();
  bindRuleBatchActions();
  bindRuleDeleteButtons();

  const filter = document.getElementById("rule-group-filter");
  const focusGroupId = parseIntVar(window.__DPM_FOCUS_GROUP__ || 0);
  const fallbackGroup = filter ? parseIntVar(filter.value) : 0;
  let activeGroupId = fallbackGroup;
  if (window.__DPM_ACTIVE_GROUP_FILTER__) {
    activeGroupId = parseIntVar(window.__DPM_ACTIVE_GROUP_FILTER__);
  }
  setCurrentRuleGroup(activeGroupId);
  updateSelectedSummary();

  const groupInput = document.getElementById("group-id");
  if (filter && groupInput) {
    const optionExists = groupInput.querySelector(`option[value="${filter.value}"]`);
    if (optionExists) {
      groupInput.value = filter.value;
    }
  }

  if (focusGroupId > 0) {
    const detail = document.getElementById(`proxy-group-detail-${focusGroupId}`);
    const card = document.getElementById(`proxy-group-card-${focusGroupId}`);
    if (card) {
      card.classList.add("proxy-group-focus");
      card.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (detail) {
      detail.open = true;
    }
  }
});

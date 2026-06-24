/* ═══════════════════════════════════════════════════════════════════════════
   GetAJob Approval Queue — Application Logic
   ═══════════════════════════════════════════════════════════════════════════
   HTMX event wiring, dashboard auto-refresh, toast notifications, modal
   management, and bulk-approve workflow.
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
    'use strict';

    // ── State ────────────────────────────────────────────────────────────

    var STATE = {
        selectedIds: new Set(),
        currentPage: typeof window.currentPage !== 'undefined' ? window.currentPage : 1,
        currentState: typeof window.currentState !== 'undefined' ? window.currentState : '',
        companyFilter: '',
        refreshInterval: null,
        isRefreshing: false,
    };

    var API_BASE = '/api';


    // ── Toast Notification System ────────────────────────────────────────

    /**
     * Show a toast notification.
     * @param {string} message - Notification text.
     * @param {'success'|'error'|'info'|'warning'} type - Toast style.
     * @param {number} duration - Time in ms before auto-dismiss.
     */
    function showToast(message, type, duration) {
        type = type || 'success';
        duration = duration || 4000;

        var container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'toast-container';
            container.setAttribute('aria-live', 'polite');
            document.body.appendChild(container);
        }

        var toast = document.createElement('div');
        toast.className = 'toast toast-' + type;
        toast.innerHTML = '<span>' + escapeHtml(message) + '</span>' +
            '<button onclick="window.dismissToast(this.parentElement)" aria-label="Dismiss">&times;</button>';

        container.appendChild(toast);

        if (duration > 0) {
            setTimeout(function () {
                dismissToast(toast);
            }, duration);
        }
    }

    /**
     * Dismiss a toast with animation.
     * @param {HTMLElement} toast
     */
    function dismissToast(toast) {
        if (!toast || toast.classList.contains('removing')) return;
        toast.classList.add('removing');
        setTimeout(function () {
            if (toast.parentElement) {
                toast.parentElement.removeChild(toast);
            }
        }, 200);
    }

    // Expose dismiss globally for inline onclick.
    window.dismissToast = dismissToast;

    /**
     * Convenience: show a success toast.
     * @param {string} msg
     */
    window.toastSuccess = function (msg) {
        showToast(msg, 'success');
    };

    /**
     * Convenience: show an error toast.
     * @param {string} msg
     */
    window.toastError = function (msg) {
        showToast(msg, 'error', 6000);
    };

    /**
     * Convenience: show an info toast.
     * @param {string} msg
     */
    window.toastInfo = function (msg) {
        showToast(msg, 'info');
    };


    // ── Dashboard Auto-Refresh ───────────────────────────────────────────

    /**
     * Fetch fresh stats from the API and update the stat cards.
     */
    function refreshStats() {
        if (STATE.isRefreshing) return;

        fetch(API_BASE + '/stats')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                updateStat('stat-pending', data.pending_review);
                updateStat('stat-approved', data.approved_today);
                updateStat('stat-submitted', data.submitted);
                updateStat('stat-failed', data.failed);
                updateBadge(data.pending_review);
            })
            .catch(function () {
                // Silently fail — stats will retry in 30s.
            });
    }

    /**
     * Update a stat card's value with animation.
     * @param {string} id - Element ID.
     * @param {number|string} value
     */
    function updateStat(id, value) {
        var el = document.getElementById(id);
        if (el && el.textContent !== String(value)) {
            el.textContent = value != null ? value : '—';
        }
    }

    /**
     * Update the pending-review badge in the sidebar.
     * @param {number} count
     */
    function updateBadge(count) {
        var badge = document.getElementById('pending-badge');
        if (badge) {
            badge.textContent = count != null ? count : '0';
        }
    }

    /**
     * Fetch fresh applications and update the table body.
     */
    function refreshApplications() {
        if (STATE.isRefreshing) return;
        STATE.isRefreshing = true;

        var params = new URLSearchParams({
            limit: '20',
            offset: String((STATE.currentPage - 1) * 20),
        });

        if (STATE.currentState) {
            params.set('state', STATE.currentState);
        }

        fetch(API_BASE + '/applications?' + params.toString())
            .then(function (r) { return r.json(); })
            .then(function (data) {
                renderTable(data.items || []);
                updatePagination(data.page, data.total_pages, data.total);
            })
            .catch(function () {
                // Silent fail — retry in 30s.
            })
            .finally(function () {
                STATE.isRefreshing = false;
            });
    }

    /**
     * Render application rows into the table body.
     * @param {Array} apps
     */
    function renderTable(apps) {
        var tbody = document.getElementById('applications-body');
        if (!tbody) return;

        if (!apps || apps.length === 0) {
            tbody.innerHTML =
                '<tr><td colspan="6" class="empty-cell">' +
                '<div class="empty-state">' +
                '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.4">' +
                '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>' +
                '<polyline points="14 2 14 8 20 8"/>' +
                '<line x1="16" y1="13" x2="8" y2="13"/>' +
                '<line x1="16" y1="17" x2="8" y2="17"/>' +
                '</svg><p>No applications found</p></div></td></tr>';
            return;
        }

        var html = '';
        apps.forEach(function (app) {
            var job = app.job_listing || {};
            var company = job.company || 'Unknown';
            var title = job.title || 'Unknown Role';
            var location = job.location || '';
            var state = app.state || 'UNKNOWN';
            var date = app.created_at ? app.created_at.slice(0, 10) : '—';
            var avatarLetter = company.charAt(0);

            var badgeHtml = getStateBadge(state);
            var actionHtml = getActionButtons(app.id, state);

            html +=
                '<tr class="app-row" data-application-id="' + escapeHtml(app.id) + '" data-state="' + escapeHtml(state) + '">' +
                '<td class="col-check"><input type="checkbox" class="app-checkbox" value="' + escapeHtml(app.id) + '" onchange="window.updateBulkBar()"></td>' +
                '<td class="col-company"><div class="company-cell"><span class="company-avatar">' + escapeHtml(avatarLetter) + '</span><span class="company-name">' + escapeHtml(company) + '</span></div></td>' +
                '<td class="col-role"><div class="role-cell"><span class="role-title">' + escapeHtml(title) + '</span>' + (location ? '<span class="role-location">' + escapeHtml(location) + '</span>' : '') + '</div></td>' +
                '<td class="col-state">' + badgeHtml + '</td>' +
                '<td class="col-date"><span class="date-text">' + escapeHtml(date) + '</span></td>' +
                '<td class="col-actions"><div class="action-buttons">' + actionHtml + '</div></td>' +
                '</tr>';
        });

        tbody.innerHTML = html;
        STATE.selectedIds.clear();
        updateBulkBar();
    }

    /**
     * Get a status badge HTML fragment for a given application state.
     * @param {string} state
     * @returns {string}
     */
    function getStateBadge(state) {
        var map = {
            'PENDING_REVIEW': ['status-pending', 'Pending'],
            'STAGED': ['status-success', 'Approved'],
            'SUBMITTED': ['status-info', 'Submitted'],
            'REJECTED': ['status-rejected', 'Rejected'],
            'FAILED': ['status-danger', 'Failed'],
            'TAILORED': ['status-warning', 'Tailored'],
            'DISCOVERED': ['status-neutral', 'Discovered'],
            'OUTREACH_PENDING': ['status-info', 'Outreach'],
        };
        var entry = map[state] || ['status-neutral', state];
        return '<span class="status-badge ' + entry[0] + '">' + entry[1] + '</span>';
    }

    /**
     * Get action buttons for a table row.
     * @param {string} appId
     * @param {string} state
     * @returns {string}
     */
    function getActionButtons(appId, state) {
        var viewBtn = '<a href="/review/' + encodeURIComponent(appId) + '" class="btn btn-sm btn-secondary" title="Review">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>' +
            '</svg></a>';

        if (state === 'PENDING_REVIEW') {
            return viewBtn +
                '<button class="btn btn-sm btn-success" ' +
                'hx-post="/api/applications/' + encodeURIComponent(appId) + '/review" ' +
                'hx-vals=\'{"action": "approve"}\' hx-trigger="click" ' +
                'hx-target="closest tr" hx-swap="outerHTML" ' +
                'hx-headers=\'{"Content-Type": "application/json"}\' title="Approve">' +
                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
                '<polyline points="20 6 9 17 4 12"/></svg></button>' +
                '<button class="btn btn-sm btn-danger" ' +
                'hx-post="/api/applications/' + encodeURIComponent(appId) + '/review" ' +
                'hx-vals=\'{"action": "reject"}\' hx-trigger="click" ' +
                'hx-target="closest tr" hx-swap="outerHTML" ' +
                'hx-headers=\'{"Content-Type": "application/json"}\' title="Reject">' +
                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
                '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
                '</svg></button>';
        }

        return viewBtn;
    }

    /**
     * Update pagination controls.
     * @param {number} page
     * @param {number} totalPages
     * @param {number} total
     */
    function updatePagination(page, totalPages, total) {
        STATE.currentPage = page || 1;

        var container = document.getElementById('pagination');
        if (!container) return;

        if (!totalPages || totalPages <= 1) {
            container.style.display = 'none';
            return;
        }
        container.style.display = 'flex';

        var prevBtn = document.getElementById('prev-page');
        var nextBtn = document.getElementById('next-page');
        var infoEl = document.getElementById('pagination-info');

        if (prevBtn) prevBtn.disabled = page <= 1;
        if (nextBtn) nextBtn.disabled = page >= totalPages;

        if (infoEl) {
            infoEl.innerHTML = '<span class="page-current">' + page + '</span>' +
                '<span class="page-sep">/</span>' +
                '<span class="page-total">' + totalPages + '</span>' +
                '<span class="page-count">(' + total + ' total)</span>';
        }
    }


    // ── Filters ──────────────────────────────────────────────────────────

    /**
     * Apply current filters and refresh the dashboard.
     */
    window.applyFilters = function () {
        var stateSelect = document.getElementById('state-filter');
        var companyInput = document.getElementById('company-search');

        STATE.currentState = stateSelect ? stateSelect.value : '';
        STATE.companyFilter = companyInput ? companyInput.value.trim().toLowerCase() : '';
        STATE.currentPage = 1;

        refreshApplications();
    };

    /**
     * Clear all filters.
     */
    window.clearFilters = function () {
        var stateSelect = document.getElementById('state-filter');
        var companyInput = document.getElementById('company-search');

        if (stateSelect) stateSelect.value = '';
        if (companyInput) companyInput.value = '';
        STATE.currentState = '';
        STATE.companyFilter = '';
        STATE.currentPage = 1;

        refreshApplications();
    };


    // ── Pagination ────────────────────────────────────────────────────────

    /**
     * Change the current page.
     * @param {number} delta - +1 or -1
     */
    window.changePage = function (delta) {
        var newPage = STATE.currentPage + delta;
        if (newPage < 1) return;
        STATE.currentPage = newPage;
        refreshApplications();
    };


    // ── Bulk Actions ──────────────────────────────────────────────────────

    /**
     * Toggle select-all checkbox.
     */
    window.toggleSelectAll = function () {
        var selectAll = document.getElementById('select-all');
        var checkboxes = document.querySelectorAll('.app-checkbox');
        var isChecked = selectAll.checked;

        checkboxes.forEach(function (cb) {
            cb.checked = isChecked;
            var id = cb.value;
            if (isChecked) {
                STATE.selectedIds.add(id);
            } else {
                STATE.selectedIds.delete(id);
            }
        });

        updateBulkBar();
    };

    /**
     * Update the bulk action bar based on selected items.
     */
    window.updateBulkBar = function () {
        var checkboxes = document.querySelectorAll('.app-checkbox:checked');
        var bar = document.getElementById('bulk-bar');
        var countEl = document.getElementById('selected-count');

        STATE.selectedIds.clear();
        checkboxes.forEach(function (cb) {
            STATE.selectedIds.add(cb.value);
        });

        var count = STATE.selectedIds.size;

        if (bar) {
            bar.style.display = count > 0 ? 'flex' : 'none';
        }
        if (countEl) {
            countEl.textContent = count + ' selected';
        }
    };

    /**
     * Bulk-approve all selected applications.
     */
    window.bulkApprove = function () {
        var ids = Array.from(STATE.selectedIds);
        if (ids.length === 0) {
            showToast('No applications selected', 'warning');
            return;
        }

        if (!confirm('Approve ' + ids.length + ' application(s)?')) return;

        var btn = document.getElementById('bulk-approve-btn');
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Approving...';
        }

        fetch(API_BASE + '/bulk-approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ application_ids: ids }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.succeeded > 0) {
                    showToast(data.succeeded + ' application(s) approved', 'success');
                }
                if (data.failed > 0) {
                    showToast(data.failed + ' application(s) failed', 'error');
                }
                STATE.selectedIds.clear();
                refreshApplications();
                refreshStats();
            })
            .catch(function () {
                showToast('Bulk approve failed', 'error');
            })
            .finally(function () {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML =
                        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
                        'style="vertical-align:middle;margin-right:4px">' +
                        '<polyline points="20 6 9 17 4 12"/></svg> Approve Selected';
                }
            });
    };

    /**
     * Clear all selections.
     */
    window.clearSelection = function () {
        var checkboxes = document.querySelectorAll('.app-checkbox');
        var selectAll = document.getElementById('select-all');

        checkboxes.forEach(function (cb) { cb.checked = false; });
        if (selectAll) selectAll.checked = false;
        STATE.selectedIds.clear();

        updateBulkBar();
    };


    // ── Review Page ──────────────────────────────────────────────────────

    /**
     * Approve the current application on the review page.
     */
    window.approveApplication = function () {
        var appId = typeof window.currentApplicationId !== 'undefined' ? window.currentApplicationId : '';
        if (!appId) return;

        var btn = document.getElementById('approve-btn');
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Approving...';
        }

        submitReview(appId, 'approve', '')
            .then(function (data) {
                if (data && data.status === 'ok') {
                    showToast('Application approved', 'success');
                    setTimeout(function () { window.location.href = '/'; }, 1000);
                } else {
                    showToast('Approval failed', 'error');
                    if (btn) btn.disabled = false;
                }
            })
            .catch(function () {
                showToast('Approval request failed', 'error');
                if (btn) btn.disabled = false;
            });
    };

    /**
     * Open a modal by ID.
     * @param {string} modalId
     */
    window.openModal = function (modalId) {
        var modal = document.getElementById(modalId);
        if (modal) modal.style.display = 'flex';
    };

    /**
     * Close a modal by ID.
     * @param {string} modalId
     */
    window.closeModal = function (modalId) {
        var modal = document.getElementById(modalId);
        if (modal) modal.style.display = 'none';
    };

    /**
     * Confirm rejection from the modal and submit.
     */
    window.confirmReject = function () {
        var appId = typeof window.currentApplicationId !== 'undefined' ? window.currentApplicationId : '';
        var reasonEl = document.getElementById('reject-reason');
        var reason = reasonEl ? reasonEl.value.trim() : '';

        if (!appId) return;

        var btn = document.querySelector('#reject-modal .btn-danger');
        if (btn) btn.disabled = true;

        submitReview(appId, 'reject', reason)
            .then(function (data) {
                if (data && data.status === 'ok') {
                    showToast('Application rejected', 'warning');
                    closeModal('reject-modal');
                    setTimeout(function () { window.location.href = '/'; }, 1000);
                } else {
                    showToast('Rejection failed', 'error');
                    if (btn) btn.disabled = false;
                }
            })
            .catch(function () {
                showToast('Rejection request failed', 'error');
                if (btn) btn.disabled = false;
            });
    };

    /**
     * Submit a review decision to the API.
     * @param {string} applicationId
     * @param {'approve'|'reject'} action
     * @param {string} reason
     * @returns {Promise}
     */
    function submitReview(applicationId, action, reason) {
        return fetch(API_BASE + '/applications/' + encodeURIComponent(applicationId) + '/review', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: action, reason: reason }),
        })
            .then(function (r) {
                if (!r.ok) {
                    return r.json().then(function (err) {
                        throw new Error(err.detail || 'Request failed');
                    });
                }
                return r.json();
            });
    }

    /**
     * Toggle card expand/collapse on the review page.
     * @param {HTMLElement} headerEl
     */
    window.toggleCard = function (headerEl) {
        var card = headerEl.closest('.card-expandable');
        if (!card) return;

        var body = card.querySelector('.card-body-expandable');
        var toggle = card.querySelector('.card-toggle');

        if (body) body.classList.toggle('collapsed');
        if (toggle) toggle.classList.toggle('collapsed');
    };


    // ── HTMX Event Wiring ────────────────────────────────────────────────

    document.addEventListener('htmx:beforeRequest', function (evt) {
        // Show loading state on the triggering element.
        var el = evt.detail.elt;
        if (el && el.tagName === 'BUTTON') {
            el._originalHtml = el.innerHTML;
            el.disabled = true;
            el.innerHTML = '<span class="loading-spinner" style="padding:0;border:none;width:20px;height:20px;display:inline-block;"></span>';
        }
    });

    document.addEventListener('htmx:afterRequest', function (evt) {
        // Restore buttons after HTMX request.
        var el = evt.detail.elt;
        if (el && el._originalHtml) {
            el.innerHTML = el._originalHtml;
            el.disabled = false;
            delete el._originalHtml;
        }
    });

    document.addEventListener('htmx:responseError', function (evt) {
        var detail = '';
        try {
            var resp = evt.detail.xhr.responseText;
            var parsed = JSON.parse(resp);
            detail = parsed.detail || parsed.message || '';
        } catch (_) {
            detail = 'Server error (' + evt.detail.xhr.status + ')';
        }
        showToast(detail || 'Request failed', 'error');
    });

    document.addEventListener('htmx:afterOnLoad', function (evt) {
        // If the response includes HX-Trigger for application-updated,
        // refresh the dashboard data.
        var xhr = evt.detail.xhr;
        var trigger = xhr && xhr.getResponseHeader('HX-Trigger');
        if (trigger && trigger.indexOf('application-updated') !== -1) {
            refreshStats();
        }
    });

    // Close modals on overlay click.
    document.addEventListener('click', function (evt) {
        if (evt.target.classList.contains('modal-overlay')) {
            evt.target.style.display = 'none';
        }
    });

    // Close modals on Escape key.
    document.addEventListener('keydown', function (evt) {
        if (evt.key === 'Escape') {
            document.querySelectorAll('.modal-overlay[style*="display: flex"]').forEach(function (m) {
                m.style.display = 'none';
            });
        }
    });


    // ── Initialisation ───────────────────────────────────────────────────

    function init() {
        // Start auto-refresh for the dashboard page.
        if (document.getElementById('stats-panel')) {
            // Initial load: stats are server-rendered, but refresh immediately
            // to get live data, then every 30s.
            setTimeout(refreshStats, 500);
            setTimeout(refreshApplications, 500);
            STATE.refreshInterval = setInterval(function () {
                refreshStats();
                refreshApplications();
            }, 30000);
        }
    }

    // Run on DOM ready.
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }


    // ── Utilities ────────────────────────────────────────────────────────

    /**
     * Escape HTML entities to prevent XSS.
     * @param {*} str
     * @returns {string}
     */
    function escapeHtml(str) {
        if (str == null) return '';
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(String(str)));
        return div.innerHTML;
    }

})();

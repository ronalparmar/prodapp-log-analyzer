/**
 * ProdApp Log Analyzer — shared UI utilities
 * Included on every page via <script src="/static/pb.js"></script>
 */

/* ──────────────────────────────────────────────────────────────────────────
   Column filter (table-level)
   Usage: call initColumnFilter('table-id') for any <table id="..."> that has
   a <tr class="filter-row"> inside its <thead>.
   Each <th> in the filter-row should contain:
     <input class="col-filter" type="text" placeholder="Filter…" />
   Columns with no input are skipped.
────────────────────────────────────────────────────────────────────────── */
function initColumnFilter(tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;

  const filterRow = table.querySelector('thead tr.filter-row');
  if (!filterRow) return;

  const inputs = Array.from(filterRow.querySelectorAll('th')).map(
    th => th.querySelector('input.col-filter')
  );

  const countEl = document.getElementById(tableId + '-count');

  function applyFilters() {
    const vals = inputs.map(inp => (inp ? inp.value.toLowerCase().trim() : ''));
    let visible = 0;
    table.querySelectorAll('tbody tr').forEach(row => {
      const cells = row.querySelectorAll('td');
      const show = vals.every((v, i) => {
        if (!v) return true;
        const cell = cells[i];
        return cell && cell.textContent.toLowerCase().includes(v);
      });
      row.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    if (countEl) {
      countEl.textContent = visible + ' row' + (visible !== 1 ? 's' : '');
    }
  }

  inputs.forEach(inp => {
    if (inp) inp.addEventListener('input', applyFilters);
  });
}

/* ──────────────────────────────────────────────────────────────────────────
   Auto-init: call initColumnFilter for every table with data-filter="true"
────────────────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('table[data-filter]').forEach(tbl => {
    if (tbl.id) initColumnFilter(tbl.id);
  });
});

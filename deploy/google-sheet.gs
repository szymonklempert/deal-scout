// Campervan helper: receives one listing row from the bot and adds it to (or updates it in) the "Listings" tab.
// Paste into the spreadsheet: Extensions > Apps Script. Setup: deploy/google-sheet.md.
//
// Columns are matched by the header names in row 1, so you can reorder them or add your own columns (notes, ratings):
// the script never touches columns it doesn't know. A status you changed in the sheet is kept on re-analysis.

const SECRET = 'PASTE-THE-SAME-SECRET-AS-GOOGLE_SHEET_SECRET-IN-.env';
const TAB = 'Listings';

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (!SECRET || SECRET.indexOf('PASTE-') === 0 || body.secret !== SECRET) {
    return reply({ ok: false, error: 'wrong secret' });
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const book = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = book.getSheetByName(TAB) || book.insertSheet(TAB);
    let header = sheet.getLastColumn() ? sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0] : [];
    if (!header.length || header.every(function (h) { return h === ''; })) {
      header = body.fields.slice();
      sheet.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
      sheet.setFrozenRows(1);
    }
    body.fields.forEach(function (f) {  // a column added in a later version of the bot goes at the end
      if (header.indexOf(f) < 0) {
        header.push(f);
        sheet.getRange(1, header.length).setValue(f).setFontWeight('bold');
      }
    });

    const numberCol = header.indexOf('number');
    const last = sheet.getLastRow();
    let rowIndex = -1;
    if (last > 1) {
      const numbers = sheet.getRange(2, numberCol + 1, last - 1, 1).getValues();
      for (let i = 0; i < numbers.length; i++) {
        if (String(numbers[i][0]) === String(body.row.number)) { rowIndex = i + 2; break; }
      }
    }
    const isNew = rowIndex < 0;
    if (isNew) rowIndex = last + 1;
    const values = isNew ? header.map(function () { return ''; })
                         : sheet.getRange(rowIndex, 1, 1, header.length).getValues()[0];
    header.forEach(function (h, i) {
      if (!(h in body.row)) return;
      if (h === 'status' && !isNew && values[i] !== '') return;  // keep the status you set by hand
      values[i] = body.row[h];
    });
    sheet.getRange(rowIndex, 1, 1, header.length).setValues([values]);
    return reply({ ok: true, row: rowIndex });
  } finally {
    lock.releaseLock();
  }
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// Suggested questions derived deterministically from the loaded schema, so
// the landing screen invites questions about the user's actual data rather
// than canned examples.

import type { Dataset, DatasetColumn, DatasetTable } from "./api";

function isNumeric(column: DatasetColumn): boolean {
  const kind = column.type.toUpperCase();
  return (
    ["DOUBLE", "FLOAT", "REAL"].includes(kind) ||
    kind.includes("INT") ||
    kind.includes("DECIMAL")
  );
}

function isDate(column: DatasetColumn): boolean {
  const kind = column.type.toUpperCase();
  return kind.includes("DATE") || kind.includes("TIMESTAMP");
}

function isCategory(column: DatasetColumn): boolean {
  return (column.distinct_values?.length ?? 0) > 1;
}

function humanize(name: string): string {
  return name.replace(/_/g, " ");
}

// Numeric columns that are identifiers rather than measures make bad
// aggregation targets; skip the obvious ones.
function isMeasure(column: DatasetColumn): boolean {
  return isNumeric(column) && !/(^|_)(id|ids)$/i.test(column.name);
}

export function suggestQuestions(dataset: Dataset): string[] {
  const suggestions: string[] = [];
  const tables = dataset.tables;

  for (const table of tables) {
    const measures = table.columns.filter(isMeasure);
    const categories = table.columns.filter(isCategory);
    const dates = table.columns.filter(isDate);
    if (measures.length === 0) {
      continue;
    }
    const measure = humanize(measures[measures.length - 1].name);
    if (categories.length > 0) {
      suggestions.push(
        `Which ${humanize(categories[0].name)} had the highest total ${measure}?`,
      );
    }
    if (categories.length > 1) {
      suggestions.push(`Chart ${measure} by ${humanize(categories[1].name)}`);
    }
    if (dates.length > 0) {
      suggestions.push(`How did ${measure} change month by month?`);
    }
  }

  const joint = jointSuggestion(tables);
  if (joint) {
    suggestions.unshift(joint);
  }
  return [...new Set(suggestions)].slice(0, 4);
}

// When two tables share a column and one of them carries a measure, propose
// the comparison that join makes possible. Among the possible directions,
// the measure is taken from the narrowest table, since a small table joined
// on a shared key is usually the reference (a target or quota) the wide
// table is compared against.
function jointSuggestion(tables: DatasetTable[]): string | null {
  let best: { shared: string; measure: string; width: number } | null = null;
  for (let i = 0; i < tables.length; i++) {
    for (let j = 0; j < tables.length; j++) {
      if (i === j) {
        continue;
      }
      const names = new Set(tables[i].columns.map((column) => column.name));
      const shared = tables[j].columns.find((column) => names.has(column.name));
      if (!shared) {
        continue;
      }
      const measure = tables[j].columns.find(
        (column) => isMeasure(column) && column.name !== shared.name,
      );
      const width = tables[j].columns.length;
      if (measure && (best === null || width < best.width)) {
        best = { shared: shared.name, measure: measure.name, width };
      }
    }
  }
  return best === null
    ? null
    : `Which ${humanize(best.shared)} beat its ${humanize(best.measure)}?`;
}

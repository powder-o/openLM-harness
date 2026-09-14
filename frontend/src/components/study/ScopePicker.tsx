import type { StudyScope } from "../../api";
import { useStore } from "../../store";

interface Props {
  notebookId: number;
  value: StudyScope;
  onChange: (scope: StudyScope) => void;
}

/** Scope picker shared by the Cards/Quiz/Guides forms: whole notebook / a
 * source / the currently selected map concept or section (SPEC §7 Study tab). */
export default function ScopePicker({ notebookId, value, onChange }: Props) {
  const store = useStore();
  const documents = store.documents.filter((d) => d.status === "ready");
  const selected = store.selectedNode;

  return (
    <div className="scope-picker">
      <select
        className="input input-sm"
        aria-label="Scope"
        value={value.kind}
        onChange={(e) => {
          const kind = e.target.value as StudyScope["kind"];
          if (kind === "notebook") onChange({ kind: "notebook", id: notebookId });
          else if (kind === "document") onChange({ kind: "document", id: documents[0]?.id ?? 0 });
          else if (kind === "concept" && selected?.conceptId != null) onChange({ kind: "concept", id: selected.conceptId });
          else if (kind === "section" && selected?.sectionBlockId != null)
            onChange({ kind: "section", id: selected.sectionBlockId });
        }}
      >
        <option value="notebook">From the whole notebook</option>
        {documents.length > 0 && <option value="document">From one source</option>}
        {selected?.type === "concept" && selected.conceptId != null && (
          <option value="concept">From the concept “{selected.label}”</option>
        )}
        {selected?.type === "section" && selected.sectionBlockId != null && (
          <option value="section">From the section “{selected.label}”</option>
        )}
      </select>
      {value.kind === "document" && (
        <select
          className="input input-sm"
          aria-label="Source"
          value={value.id}
          onChange={(e) => onChange({ kind: "document", id: Number(e.target.value) })}
        >
          {documents.map((d) => (
            <option key={d.id} value={d.id}>
              {d.title}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

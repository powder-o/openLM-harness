interface Props {
  /** 0..1, or null when there is no mastery signal yet. */
  value: number | null | undefined;
  wide?: boolean;
}

/** Mastery printed as the press's ten-step wedge. */
export default function Wedge({ value, wide = false }: Props) {
  const on = value == null ? 0 : Math.round(Math.max(0, Math.min(1, value)) * 10);
  return (
    <div
      className={`wedge ${wide ? "wedge-wide" : ""}`}
      role="img"
      aria-label={value == null ? "No mastery yet" : `Mastery ${Math.round(value * 100)}%`}
    >
      {Array.from({ length: 10 }, (_, i) => (
        <span key={i} className={`wedge-cell ${i < on ? "on" : ""}`} />
      ))}
    </div>
  );
}

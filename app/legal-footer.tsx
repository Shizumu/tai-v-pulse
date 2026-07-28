type LegalFooterProps = {
  context: string;
  note: string;
};

export default function LegalFooter({ context, note }: LegalFooterProps) {
  return (
    <footer className="legal-footer">
      <span>台V Pulse · {context}</span>
      <span>{note} · 非 YouTube 官方產品 · <a href="/legal">使用、隱私與授權聲明</a></span>
    </footer>
  );
}

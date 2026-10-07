/*
 * Google sends people back here after they sign in. The Supabase client
 * finishes the sign-in from the URL, then AuthProvider takes them on to
 * the page they were opening.
 */
export default function Page() {
  return (
    <div className="ws-empty">
      <div className="spinner" />
      <h2>Signing you in…</h2>
    </div>
  );
}

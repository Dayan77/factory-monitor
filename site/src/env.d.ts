interface ImportMetaEnv {
  readonly PUBLIC_APP_URL?: string;
  readonly PUBLIC_API_URL?: string;
  readonly PUBLIC_CONTACT_EMAIL?: string;
  readonly PUBLIC_DEMO_EVENT_URL?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}

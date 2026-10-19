import { ChatShell } from "@/components/ChatShell";

// The page is only reached signed in (proxy.ts). Everything on it runs in the browser.
export default function Home() {
  return <ChatShell />;
}

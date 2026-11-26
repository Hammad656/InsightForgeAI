import { AuthForm } from "@/components/auth-form";

import { SessionEntry } from "@/components/session-entry";

export default function Login() {
  return <SessionEntry><AuthForm /></SessionEntry>;
}

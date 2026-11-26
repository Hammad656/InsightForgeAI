import { AuthForm } from "@/components/auth-form";

import { SessionEntry } from "@/components/session-entry";

export default function Register() {
  return <SessionEntry><AuthForm register /></SessionEntry>;
}

import QuizPage from "../page";

export async function generateStaticParams() {
  // Desktop uses /quiz?slug=... because arbitrary server-generated slugs cannot
  // each be emitted as HTML. This entry satisfies Next's static export contract;
  // normal web deployments continue resolving arbitrary slugs dynamically.
  return process.env.DESKTOP_BUILD === "1" ? [{ slug: "desktop" }] : [];
}

export default function SlugQuizPage() {
  return <QuizPage />;
}

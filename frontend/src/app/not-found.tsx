import Link from "next/link";
import { SearchX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";

export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-screen max-w-lg items-center px-4">
      <Empty className="border bg-card py-12">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <SearchX aria-hidden />
          </EmptyMedia>
          <EmptyTitle className="text-base">Page not found</EmptyTitle>
          <EmptyDescription>That address doesn&apos;t match anything in Level 10.</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button asChild>
            <Link href="/">Go to my teams</Link>
          </Button>
        </EmptyContent>
      </Empty>
    </div>
  );
}

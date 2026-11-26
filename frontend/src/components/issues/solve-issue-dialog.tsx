"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "@/lib/zod";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { useSolveIssue } from "@/lib/api/mutations";
import type { Issue } from "@/lib/api/types";

const schema = z.object({
  resolution_note: z
    .string()
    .trim()
    .min(1, "Write how it was solved. A note is required.")
    .max(2000, "Keep the note to 2,000 characters or fewer."),
});

type FormValues = z.infer<typeof schema>;

export interface SolveIssueDialogProps {
  teamId: string;
  issue: Issue;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SolveIssueDialog({ teamId, issue, open, onOpenChange }: SolveIssueDialogProps) {
  const solve = useSolveIssue(teamId);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { resolution_note: "" } });

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  function onSubmit(values: FormValues) {
    solve.mutate(
      { issueId: issue.id, body: { resolution_note: values.resolution_note } },
      {
        onSuccess: () => {
          toast.success("Issue solved", { description: issue.title });
          handleOpenChange(false);
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>Solve issue</DialogTitle>
            <DialogDescription>{issue.title}</DialogDescription>
          </DialogHeader>
          <Field data-invalid={errors.resolution_note ? true : undefined}>
            <FieldLabel htmlFor={`solve-note-${issue.id}`}>How was it solved?</FieldLabel>
            <Textarea
              id={`solve-note-${issue.id}`}
              rows={4}
              autoFocus
              placeholder="The decision, who owns it, and what happens next."
              aria-invalid={errors.resolution_note ? true : undefined}
              {...register("resolution_note")}
            />
            <FieldDescription>Saved with the issue in the Solved list.</FieldDescription>
            <FieldError errors={[errors.resolution_note]} />
          </Field>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={solve.isPending}>
              {solve.isPending ? <Spinner data-icon="inline-start" /> : null}
              Solve issue
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

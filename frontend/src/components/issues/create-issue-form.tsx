"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Plus } from "lucide-react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "@/lib/zod";
import { PRIORITY_LEVELS } from "@/components/issues/priority-picker";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { useCreateIssue } from "@/lib/api/mutations";

const schema = z.object({
  title: z.string().trim().min(1, "Give the issue a title.").max(200, "Keep the title to 200 characters or fewer."),
  // The Select works in strings; "none" means no priority.
  priority: z.enum(["none", "1", "2", "3", "4", "5"]),
});

type FormValues = z.infer<typeof schema>;

export function CreateIssueForm({ teamId }: { teamId: string }) {
  const createIssue = useCreateIssue(teamId);
  const {
    register,
    control,
    handleSubmit,
    reset,
    setFocus,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { title: "", priority: "none" },
  });

  function onSubmit(values: FormValues) {
    createIssue.mutate(
      { title: values.title, priority: values.priority === "none" ? null : Number(values.priority) },
      {
        onSuccess: (issue) => {
          toast.success("Issue added", { description: issue.title });
          reset({ title: "", priority: values.priority });
          setFocus("title");
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-start gap-2">
        <Field className="min-w-56 flex-1" data-invalid={errors.title ? true : undefined}>
          <FieldLabel htmlFor="new-issue-title" className="sr-only">
            New issue title
          </FieldLabel>
          <Input
            id="new-issue-title"
            placeholder="Add an issue, e.g. Pipeline is thin for next quarter"
            autoComplete="off"
            aria-invalid={errors.title ? true : undefined}
            {...register("title")}
          />
        </Field>
        <Field className="w-36">
          <FieldLabel htmlFor="new-issue-priority" className="sr-only">
            Priority (optional)
          </FieldLabel>
          <Controller
            control={control}
            name="priority"
            render={({ field }) => (
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger id="new-issue-priority" className="w-full">
                  <SelectValue placeholder="Priority" />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value="none">No priority</SelectItem>
                    {PRIORITY_LEVELS.map((level) => (
                      <SelectItem key={level} value={String(level)}>
                        Priority {level}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            )}
          />
        </Field>
        <Button type="submit" disabled={createIssue.isPending}>
          {createIssue.isPending ? <Spinner data-icon="inline-start" /> : <Plus data-icon="inline-start" aria-hidden />}
          Add issue
        </Button>
      </div>
      <FieldError errors={[errors.title]} />
    </form>
  );
}

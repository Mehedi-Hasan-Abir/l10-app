"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { CalendarClock } from "lucide-react";
import { Controller, useForm } from "react-hook-form";
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
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { useAddTodoToIssue } from "@/lib/api/mutations";
import { useMe } from "@/lib/api/queries";
import type { Issue, UserRef } from "@/lib/api/types";
import { formatDateWithWeekday } from "@/lib/time";

const schema = z.object({
  title: z
    .string()
    .trim()
    .min(1, "Describe the follow-up action.")
    .max(200, "Keep the title to 200 characters or fewer."),
  owner_id: z.string().min(1, "Choose who owns this To-Do."),
});

type FormValues = z.infer<typeof schema>;

export interface AddTodoDialogProps {
  teamId: string;
  issue: Issue;
  eligibleOwners: UserRef[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddTodoDialog({ teamId, issue, eligibleOwners, open, onOpenChange }: AddTodoDialogProps) {
  const me = useMe().data?.user;
  const addTodo = useAddTodoToIssue(teamId);

  // Default to the person typing, when they are allowed to own a To-Do.
  const defaultOwner = me && eligibleOwners.some((o) => o.id === me.id) ? me.id : "";

  const {
    register,
    control,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { title: "", owner_id: defaultOwner },
  });

  function handleOpenChange(next: boolean) {
    if (!next) reset({ title: "", owner_id: defaultOwner });
    onOpenChange(next);
  }

  function onSubmit(values: FormValues) {
    addTodo.mutate(
      { issueId: issue.id, body: { title: values.title, owner_id: values.owner_id } },
      {
        onSuccess: (todo) => {
          toast.success("To-Do added", {
            description: `${todo.title} · ${todo.owner.name} · due ${formatDateWithWeekday(todo.due_date)}`,
          });
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
            <DialogTitle>Add a To-Do</DialogTitle>
            <DialogDescription>Follow-up for “{issue.title}”.</DialogDescription>
          </DialogHeader>

          <Field data-invalid={errors.title ? true : undefined}>
            <FieldLabel htmlFor={`todo-title-${issue.id}`}>To-Do</FieldLabel>
            <Input
              id={`todo-title-${issue.id}`}
              autoFocus
              autoComplete="off"
              placeholder="What needs to happen?"
              aria-invalid={errors.title ? true : undefined}
              {...register("title")}
            />
            <FieldError errors={[errors.title]} />
          </Field>

          <Field data-invalid={errors.owner_id ? true : undefined}>
            <FieldLabel htmlFor={`todo-owner-${issue.id}`}>Owner</FieldLabel>
            <Controller
              control={control}
              name="owner_id"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger
                    id={`todo-owner-${issue.id}`}
                    className="w-full"
                    aria-invalid={errors.owner_id ? true : undefined}
                  >
                    <SelectValue placeholder="Choose an owner" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {eligibleOwners.map((owner) => (
                        <SelectItem key={owner.id} value={owner.id}>
                          {owner.name}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              )}
            />
            <FieldError errors={[errors.owner_id]} />
          </Field>

          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <CalendarClock className="size-4" aria-hidden />
            Due in 7 days. It will be linked to this issue.
          </p>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={addTodo.isPending}>
              {addTodo.isPending ? <Spinner data-icon="inline-start" /> : null}
              Add To-Do
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

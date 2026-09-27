import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../config/api_config.dart';
import '../data/models/attachment.dart';
import '../data/models/comment.dart';
import '../data/models/custom_field_definition.dart';
import '../data/models/lead.dart' show Priority;
import '../data/models/task.dart';
import '../services/api_service.dart';
import '../services/org_date.dart';

export '../services/api_service.dart' show ApiResponse;

/// Full task-detail snapshot returned by `GET /api/tasks/{id}/`.
/// Bundles everything the detail screen needs in one round-trip so we don't
/// chase the same endpoint twice for comments / attachments / CF schema.
class TaskDetailResult {
  final Task task;
  final List<Comment> comments;
  final List<Attachment> attachments;
  final List<CustomFieldDefinition> customFieldDefinitions;

  const TaskDetailResult({
    required this.task,
    this.comments = const [],
    this.attachments = const [],
    this.customFieldDefinitions = const [],
  });

  TaskDetailResult copyWith({
    Task? task,
    List<Comment>? comments,
    List<Attachment>? attachments,
    List<CustomFieldDefinition>? customFieldDefinitions,
  }) {
    return TaskDetailResult(
      task: task ?? this.task,
      comments: comments ?? this.comments,
      attachments: attachments ?? this.attachments,
      customFieldDefinitions:
          customFieldDefinitions ?? this.customFieldDefinitions,
    );
  }
}

/// Server-side filter set for the tasks list. Held as an immutable value so
/// the screen can diff "did the user actually change something" before
/// triggering a refetch.
class TaskFilters {
  final String? search;
  // A set rather than one value, because the dashboard's Overdue and Due Today
  // badges both mean "New or In Progress". The filter sheet still offers one
  // status at a time and stores it as a single-element set; the API takes a
  // repeated `?status=` for each member.
  final Set<TaskStatus> statuses;
  final Priority? priority;
  // Assignee profile id. null = anyone (no filter).
  final String? assignedToId;
  // Display label for the active assignee filter (e.g. "Me"). Not sent to
  // backend; used by the UI to render the chip without a profile lookup.
  final String? assignedToLabel;
  // ISO date strings (yyyy-MM-dd), backend supports due_date__gte/__lte.
  // Used by the calendar view to fetch only the visible month.
  final String? dueDateGte;
  final String? dueDateLte;

  const TaskFilters({
    this.search,
    this.statuses = const {},
    this.priority,
    this.assignedToId,
    this.assignedToLabel,
    this.dueDateGte,
    this.dueDateLte,
  });

  /// The single selected status, or null when none or several are selected.
  /// The filter sheet is single-select, so this is what it reads and shows.
  TaskStatus? get status => statuses.length == 1 ? statuses.first : null;

  /// Statuses a task can still be acted on in. The dashboard's Overdue and Due
  /// Today counts are both scoped to these, so a list opened from either badge
  /// has to be scoped the same way or it shows a different number than the
  /// badge the user tapped.
  static const openStatuses = {TaskStatus.newTask, TaskStatus.inProgress};

  /// The dashboard's "Overdue" badge: still open, and due before today.
  /// Mirrors `ApiHomeView`'s `due_date__lt=today`; `__lte` on the day before
  /// is the same set for a date column and is what the list endpoint takes.
  factory TaskFilters.overdue(DateTime today) => TaskFilters(
    statuses: openStatuses,
    dueDateLte: _isoDay(addDays(today, -1)),
  );

  /// The dashboard's "Due Today" badge: still open, due on the given day.
  factory TaskFilters.dueOn(DateTime day) => TaskFilters(
    statuses: openStatuses,
    dueDateGte: _isoDay(day),
    dueDateLte: _isoDay(day),
  );

  static String _isoDay(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';

  bool get isEmpty =>
      (search == null || search!.isEmpty) &&
      statuses.isEmpty &&
      priority == null &&
      (assignedToId == null || assignedToId!.isEmpty);

  bool get isActive => !isEmpty;

  TaskFilters copyWith({
    String? search,
    TaskStatus? status,
    Set<TaskStatus>? statuses,
    Priority? priority,
    String? assignedToId,
    String? assignedToLabel,
    String? dueDateGte,
    String? dueDateLte,
  }) {
    return TaskFilters(
      search: search ?? this.search,
      statuses: statuses ?? (status != null ? {status} : this.statuses),
      priority: priority ?? this.priority,
      assignedToId: assignedToId ?? this.assignedToId,
      assignedToLabel: assignedToLabel ?? this.assignedToLabel,
      dueDateGte: dueDateGte ?? this.dueDateGte,
      dueDateLte: dueDateLte ?? this.dueDateLte,
    );
  }

  /// copyWith can't distinguish "unchanged" from "set to null", so use this
  /// to explicitly clear individual fields.
  TaskFilters cleared({
    bool search = false,
    bool status = false,
    bool priority = false,
    bool assignedTo = false,
    bool dueDate = false,
  }) {
    return TaskFilters(
      search: search ? null : this.search,
      statuses: status ? const {} : statuses,
      priority: priority ? null : this.priority,
      assignedToId: assignedTo ? null : assignedToId,
      assignedToLabel: assignedTo ? null : assignedToLabel,
      dueDateGte: dueDate ? null : dueDateGte,
      dueDateLte: dueDate ? null : dueDateLte,
    );
  }
}

/// Paginated tasks snapshot, wrapped by AsyncValue.
class TasksListData {
  final List<Task> tasks;
  final int totalCount;
  final bool hasMore;
  final int currentOffset;

  const TasksListData({
    this.tasks = const [],
    this.totalCount = 0,
    this.hasMore = true,
    this.currentOffset = 0,
  });

  TasksListData copyWith({
    List<Task>? tasks,
    int? totalCount,
    bool? hasMore,
    int? currentOffset,
  }) {
    return TasksListData(
      tasks: tasks ?? this.tasks,
      totalCount: totalCount ?? this.totalCount,
      hasMore: hasMore ?? this.hasMore,
      currentOffset: currentOffset ?? this.currentOffset,
    );
  }
}

class TasksNotifier extends AsyncNotifier<TasksListData> {
  final ApiService _apiService = ApiService();
  static const int _pageSize = 20;

  TaskFilters _filters = const TaskFilters();
  TaskFilters get filters => _filters;

  @override
  Future<TasksListData> build() => _fetchPage(offset: 0);

  /// Replace filters and refetch from offset 0. Use this from the screen
  /// when chips/search change so the in-flight queryParams stay consistent
  /// across `refresh()` and `loadMore()`.
  Future<void> setFilters(TaskFilters filters) async {
    _filters = filters;
    await refresh();
  }

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(() => _fetchPage(offset: 0));
  }

  Future<void> loadMore() async {
    final current = state.value;
    if (current == null || !current.hasMore) return;
    if (state.isLoading) return;

    state = await AsyncValue.guard(() async {
      final next = await _fetchPage(offset: current.currentOffset);
      return current.copyWith(
        tasks: [...current.tasks, ...next.tasks],
        totalCount: next.totalCount,
        hasMore: next.hasMore,
        currentOffset: next.currentOffset,
      );
    });
  }

  Future<TasksListData> _fetchPage({required int offset}) async {
    // `dynamic` so a value can be a List: `Uri.replace` turns one into a
    // repeated parameter, which is how the API takes more than one status.
    final queryParams = <String, dynamic>{
      'limit': _pageSize.toString(),
      'offset': offset.toString(),
    };
    final f = _filters;
    if (f.search != null && f.search!.isNotEmpty) {
      queryParams['search'] = f.search!;
    }
    if (f.statuses.isNotEmpty) {
      queryParams['status'] = f.statuses.map((s) => s.value).toList();
    }
    if (f.priority != null) queryParams['priority'] = f.priority!.label;
    if (f.assignedToId != null && f.assignedToId!.isNotEmpty) {
      queryParams['assigned_to'] = f.assignedToId!;
    }
    if (f.dueDateGte != null && f.dueDateGte!.isNotEmpty) {
      queryParams['due_date__gte'] = f.dueDateGte!;
    }
    if (f.dueDateLte != null && f.dueDateLte!.isNotEmpty) {
      queryParams['due_date__lte'] = f.dueDateLte!;
    }

    final url = Uri.parse(
      ApiConfig.tasks,
    ).replace(queryParameters: queryParams).toString();
    debugPrint('TasksNotifier: Fetching tasks (offset: $offset)...');
    final response = await _apiService.get(url);

    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load tasks');
    }

    final data = response.data!;
    debugPrint('TasksNotifier: API response keys: ${data.keys.toList()}');

    List<dynamic> tasksList = [];
    int tasksCount = 0;

    if (data['tasks'] != null) {
      tasksList = data['tasks'] as List<dynamic>? ?? [];
      tasksCount = data['tasks_count'] as int? ?? tasksList.length;
    } else if (data['results'] != null) {
      tasksList = data['results'] as List<dynamic>? ?? [];
      tasksCount = data['count'] as int? ?? tasksList.length;
    }

    final newTasks = <Task>[];
    for (final item in tasksList) {
      try {
        if (item is Map<String, dynamic>) {
          newTasks.add(Task.fromJson(item));
        }
      } catch (e) {
        debugPrint('TasksNotifier: Error parsing task: $e');
      }
    }

    debugPrint('TasksNotifier: loaded ${newTasks.length} tasks');

    return TasksListData(
      tasks: newTasks,
      totalCount: tasksCount,
      hasMore: newTasks.length >= _pageSize,
      currentOffset: offset + newTasks.length,
    );
  }

  /// Fetch a single task from the API.
  Future<Task?> getTaskById(String taskId) async {
    final detail = await getTaskDetail(taskId);
    return detail?.task;
  }

  /// Fetch full task detail incl. comments, attachments and custom-field
  /// schema. The detail endpoint returns all of these in one payload. Splitting
  /// them into separate models lets the UI render each section without
  /// re-parsing the raw map.
  Future<TaskDetailResult?> getTaskDetail(String taskId) async {
    try {
      final url = '${ApiConfig.tasks}$taskId/';
      final response = await _apiService.get(url);
      if (!response.success || response.data == null) {
        debugPrint('TasksNotifier: detail fetch failed - ${response.message}');
        return null;
      }
      final data = response.data!;
      final taskData = data['task_obj'] as Map<String, dynamic>?;
      if (taskData == null) return null;

      final comments = <Comment>[];
      for (final c in (data['comments'] as List<dynamic>? ?? [])) {
        if (c is Map<String, dynamic>) comments.add(Comment.fromJson(c));
      }
      final attachments = <Attachment>[];
      for (final a in (data['attachments'] as List<dynamic>? ?? [])) {
        if (a is Map<String, dynamic>) attachments.add(Attachment.fromJson(a));
      }
      final defs = <CustomFieldDefinition>[];
      for (final d
          in (data['custom_field_definitions'] as List<dynamic>? ?? [])) {
        if (d is Map<String, dynamic>) {
          defs.add(CustomFieldDefinition.fromJson(d));
        }
      }
      defs.sort((a, b) {
        final byOrder = a.displayOrder.compareTo(b.displayOrder);
        return byOrder != 0 ? byOrder : a.label.compareTo(b.label);
      });

      return TaskDetailResult(
        task: Task.fromJson(taskData),
        comments: comments,
        attachments: attachments,
        customFieldDefinitions: defs,
      );
    } catch (e) {
      debugPrint('TasksNotifier: Exception getting task detail - $e');
      return null;
    }
  }

  /// Post a new comment to a task. Backend uses the same POST as the
  /// attachment-upload endpoint (`TaskDetailView.post`), and echoes the
  /// refreshed `comments` + `attachments` arrays in the response.
  Future<ApiResponse<Map<String, dynamic>>> addTaskComment(
    String taskId,
    String comment,
  ) async {
    try {
      final url = '${ApiConfig.tasks}$taskId/';
      return await _apiService.post(url, {'comment': comment});
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Delete one comment by its UUID. Routed via `/api/tasks/comment/{id}/`.
  Future<ApiResponse<Map<String, dynamic>>> deleteTaskComment(
    String commentId,
  ) async {
    try {
      final url = '${ApiConfig.tasks}comment/$commentId/';
      return await _apiService.delete(url);
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Delete one attachment by its UUID. Routed via `/api/tasks/attachment/{id}/`.
  Future<ApiResponse<Map<String, dynamic>>> deleteTaskAttachment(
    String attachmentId,
  ) async {
    try {
      final url = '${ApiConfig.tasks}attachment/$attachmentId/';
      return await _apiService.delete(url);
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> createTask(
    Map<String, dynamic> data,
  ) async {
    try {
      final response = await _apiService.post(ApiConfig.tasks, data);
      if (response.success) await refresh();
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> updateTask(
    String taskId,
    Map<String, dynamic> data,
  ) async {
    try {
      final url = '${ApiConfig.tasks}$taskId/';
      // PATCH (not PUT) so M2M fields the form doesn't touch; `tags`,
      // `contacts`, `teams`, are left alone. The backend PUT handler
      // unconditionally clears those M2Ms before reading the request body
      // (tasks/views/task_views.py), so a mobile PUT silently wipes them.
      // PATCH only mutates keys present in the payload.
      final response = await _apiService.patch(url, data);
      if (response.success) await refresh();
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> deleteTask(String taskId) async {
    try {
      final url = '${ApiConfig.tasks}$taskId/';
      final response = await _apiService.delete(url);

      if (response.success || response.statusCode == 204) {
        final current = state.value;
        if (current != null) {
          state = AsyncValue.data(
            current.copyWith(
              tasks: current.tasks.where((t) => t.id != taskId).toList(),
              totalCount: current.totalCount - 1,
            ),
          );
        }
        return ApiResponse(success: true, statusCode: 204);
      }

      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Toggle a task's completion (helper for the UI checkbox).
  Future<ApiResponse<Map<String, dynamic>>> toggleTaskStatus(Task task) async {
    final newStatus = task.completed ? 'New' : 'Completed';
    return patchTask(task.id, {'status': newStatus});
  }

  /// Partially update a task. Optimistically applies changes locally.
  Future<ApiResponse<Map<String, dynamic>>> patchTask(
    String taskId,
    Map<String, dynamic> data,
  ) async {
    try {
      final url = '${ApiConfig.tasks}$taskId/';
      final response = await _apiService.patch(url, data);

      if (response.success) {
        final current = state.value;
        if (current != null) {
          state = AsyncValue.data(
            current.copyWith(
              tasks: current.tasks.map((t) {
                if (t.id == taskId) {
                  return t.copyWith(
                    status: data.containsKey('status')
                        ? TaskStatus.fromString(data['status'] as String?)
                        : null,
                    priority: data.containsKey('priority')
                        ? Priority.fromString(data['priority'] as String?)
                        : null,
                    title: data['title'] as String?,
                    description: data['description'] as String?,
                  );
                }
                return t;
              }).toList(),
            ),
          );
        }
      }

      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }
}

final tasksProvider = AsyncNotifierProvider<TasksNotifier, TasksListData>(
  TasksNotifier.new,
);

/// Convenience providers. Read from the AsyncValue.
final tasksListProvider = Provider<List<Task>>((ref) {
  return ref.watch(tasksProvider).value?.tasks ?? const [];
});

final tasksLoadingProvider = Provider<bool>((ref) {
  return ref.watch(tasksProvider).isLoading;
});

final tasksErrorProvider = Provider<String?>((ref) {
  return ref.watch(tasksProvider).error?.toString();
});

/// Grouped tasks providers (filters / sorters on top of the loaded list).
final overdueTasksProvider = Provider<List<Task>>((ref) {
  final tasks = ref.watch(tasksListProvider);
  return tasks.where((t) => t.isOverdue).toList()..sort((a, b) {
    if (a.dueDate == null) return 1;
    if (b.dueDate == null) return -1;
    return a.dueDate!.compareTo(b.dueDate!);
  });
});

final todayTasksProvider = Provider<List<Task>>((ref) {
  final tasks = ref.watch(tasksListProvider);
  return tasks.where((t) => !t.completed && t.isDueToday).toList()
    ..sort((a, b) {
      if (a.dueDate == null) return 1;
      if (b.dueDate == null) return -1;
      return a.dueDate!.compareTo(b.dueDate!);
    });
});

final upcomingTasksProvider = Provider<List<Task>>((ref) {
  final tasks = ref.watch(tasksListProvider);
  return tasks.where((t) => t.isUpcoming).toList()..sort((a, b) {
    if (a.dueDate == null) return 1;
    if (b.dueDate == null) return -1;
    return a.dueDate!.compareTo(b.dueDate!);
  });
});

final completedTasksProvider = Provider<List<Task>>((ref) {
  final tasks = ref.watch(tasksListProvider);
  return tasks.where((t) => t.completed).toList();
});

final noDueDateTasksProvider = Provider<List<Task>>((ref) {
  final tasks = ref.watch(tasksListProvider);
  return tasks.where((t) => !t.completed && t.dueDate == null).toList();
});

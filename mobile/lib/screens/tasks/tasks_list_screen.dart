import '../../services/org_date.dart';
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:table_calendar/table_calendar.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';
import '../../providers/profile_provider.dart';
import '../../providers/tasks_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/cards/task_row.dart';
import '../../widgets/common/common.dart';

enum TaskViewMode { calendar, list }

/// Tasks List Screen
/// Calendar and List views for task management
class TasksListScreen extends ConsumerStatefulWidget {
  const TasksListScreen({super.key, this.initialView});

  /// One of `AppRoutes.viewOverdue` / `AppRoutes.viewDueToday`, when the user
  /// arrived by tapping the matching dashboard badge. Anything else, including
  /// null, opens the list unfiltered.
  final String? initialView;

  @override
  ConsumerState<TasksListScreen> createState() => _TasksListScreenState();
}

class _TasksListScreenState extends ConsumerState<TasksListScreen> {
  TaskViewMode _viewMode = TaskViewMode.list;
  DateTime _focusedDay = DateTime.now();
  DateTime _selectedDay = DateTime.now();
  CalendarFormat _calendarFormat = CalendarFormat.month;

  final ScrollController _scrollController = ScrollController();
  final TextEditingController _searchController = TextEditingController();
  Timer? _searchDebounce;
  TaskFilters _filters = const TaskFilters();
  // The dashboard badge this list was opened from, while its filter is still
  // the one in force. Cleared the moment the user changes anything, because
  // after that the label would be describing a set it no longer selects.
  String? _activeView;

  static const _searchDebounceMs = 350;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    _applyInitialView();
  }

  /// Open already narrowed to what a dashboard badge counted.
  ///
  /// The notifier outlives this screen, so its filters have to be pushed at it
  /// rather than passed in; the post-frame hop is because the first build is
  /// still running when initState does. The definitions live on `TaskFilters`
  /// so they can be tested against the server's, which is the only thing that
  /// keeps the badge and the list showing the same number.
  void _applyInitialView() {
    final TaskFilters? initial = switch (widget.initialView) {
      // The org's today: `ApiHomeView` counts the badge on the org's day.
      AppRoutes.viewOverdue => TaskFilters.overdue(orgToday()),
      AppRoutes.viewDueToday => TaskFilters.dueOn(orgToday()),
      _ => null,
    };
    if (initial == null) return;
    _filters = initial;
    _activeView = widget.initialView;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) ref.read(tasksProvider.notifier).setFilters(initial);
    });
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _searchController.dispose();
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    super.dispose();
  }

  /// Trigger paginated fetch when the user scrolls near the bottom. Without
  /// this, the provider's loadMore() never runs and users silently see only
  /// the first page (20 tasks).
  void _onScroll() {
    if (_scrollController.position.pixels >=
        _scrollController.position.maxScrollExtent - 200) {
      ref.read(tasksProvider.notifier).loadMore();
    }
  }

  void _applyFilters(TaskFilters next) {
    setState(() {
      _filters = next;
      _activeView = null;
    });
    ref.read(tasksProvider.notifier).setFilters(next);
  }

  void _onSearchChanged(String value) {
    setState(() {}); // refresh clear-button visibility
    _searchDebounce?.cancel();
    _searchDebounce = Timer(
      const Duration(milliseconds: _searchDebounceMs),
      () => _applyFilters(_filters.copyWith(search: value.trim())),
    );
  }

  void _clearSearch() {
    _searchController.clear();
    _searchDebounce?.cancel();
    _applyFilters(_filters.cleared(search: true));
  }

  void _clearAllFilters() {
    _searchController.clear();
    _searchDebounce?.cancel();
    _applyFilters(const TaskFilters());
  }

  // Get task dates for calendar markers
  Set<String> _getTaskDates(List<Task> tasks) {
    return tasks
        .where((t) => t.dueDate != null)
        .map((t) => _formatDateKey(t.dueDate!))
        .toSet();
  }

  // Tasks for selected date (Calendar view)
  List<Task> _getTasksForSelectedDate(List<Task> tasks) {
    return tasks
        .where((t) => t.dueDate != null && _isSameDay(t.dueDate!, _selectedDay))
        .toList()
      ..sort((a, b) {
        if (a.dueDate == null) return 1;
        if (b.dueDate == null) return -1;
        return a.dueDate!.compareTo(b.dueDate!);
      });
  }

  /// Set due-date range to the visible month so calendar markers cover every
  /// task in that month, not just the 20 currently paged in.
  void _fetchMonthForCalendar(DateTime focusedDay) {
    final first = DateTime(focusedDay.year, focusedDay.month, 1);
    final last = DateTime(focusedDay.year, focusedDay.month + 1, 0);
    final next = _filters.copyWith(
      dueDateGte: _formatDateKey(first),
      dueDateLte: _formatDateKey(last),
    );
    _applyFilters(next);
  }

  void _toggleViewMode() {
    setState(() {
      _viewMode = _viewMode == TaskViewMode.calendar
          ? TaskViewMode.list
          : TaskViewMode.calendar;
    });
    if (_viewMode == TaskViewMode.calendar) {
      _fetchMonthForCalendar(_focusedDay);
    } else if (_filters.dueDateGte != null || _filters.dueDateLte != null) {
      // Drop the month window when returning to list view so the user sees
      // all their tasks again.
      _applyFilters(_filters.cleared(dueDate: true));
    }
  }

  @override
  Widget build(BuildContext context) {
    final tasksAsync = ref.watch(tasksProvider);
    // Eagerly subscribe so the "My tasks" toggle can read profile.id without
    // waiting for a separate fetch when the user taps it.
    ref.watch(profileProvider);
    final allTasks = tasksAsync.value?.tasks ?? const <Task>[];
    final isLoading = tasksAsync.isLoading;
    final error = tasksAsync.error?.toString();

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Tasks'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          IconButton(
            icon: Icon(
              _viewMode == TaskViewMode.calendar
                  ? LucideIcons.list
                  : LucideIcons.calendar,
              size: 22,
            ),
            onPressed: _toggleViewMode,
          ),
          // The board is a different record type, not another view of these
          // tasks, so it is a place to go rather than a mode to toggle.
          IconButton(
            tooltip: 'Boards',
            icon: const Icon(LucideIcons.squareKanban, size: 22),
            onPressed: () => context.push(AppRoutes.taskBoard),
          ),
          IconButton(
            icon: const Icon(LucideIcons.plus, size: 22),
            onPressed: () => _navigateToCreateTask(),
          ),
        ],
      ),
      body: Column(
        children: [
          if (_viewMode == TaskViewMode.list) ...[
            _buildSearchBar(),
            _buildFilterBar(),
          ],
          Expanded(child: _buildBody(allTasks, isLoading, error)),
        ],
      ),
    );
  }

  Widget _buildBody(List<Task> allTasks, bool isLoading, String? error) {
    if (isLoading && allTasks.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    if (error != null && allTasks.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                LucideIcons.alertCircle,
                size: 48,
                color: AppColors.danger500,
              ),
              const SizedBox(height: 16),
              Text('Failed to load tasks', style: AppTypography.h3),
              const SizedBox(height: 8),
              Text(
                error,
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 24),
              PrimaryButton(
                label: 'Retry',
                icon: LucideIcons.refreshCw,
                onPressed: () {
                  ref.read(tasksProvider.notifier).refresh();
                },
              ),
            ],
          ),
        ),
      );
    }

    return AnimatedSwitcher(
      duration: AppDurations.normal,
      child: _viewMode == TaskViewMode.calendar
          ? _buildCalendarView(allTasks)
          : _buildListView(allTasks, isLoading),
    );
  }

  Widget _buildSearchBar() {
    final hasQuery = _searchController.text.isNotEmpty;
    return Container(
      color: AppColors.surfaceDim,
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 6),
      child: TextField(
        controller: _searchController,
        onChanged: _onSearchChanged,
        style: AppTypography.body,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          hintText: 'Search task title',
          hintStyle: AppTypography.body.copyWith(color: AppColors.textTertiary),
          prefixIcon: Icon(
            LucideIcons.search,
            color: AppColors.textTertiary,
            size: 18,
          ),
          suffixIcon: hasQuery
              ? IconButton(
                  icon: Icon(
                    LucideIcons.x,
                    color: AppColors.textTertiary,
                    size: 16,
                  ),
                  onPressed: _clearSearch,
                )
              : null,
          filled: true,
          fillColor: AppColors.gray100,
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 12,
            vertical: 10,
          ),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide.none,
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide.none,
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide(color: AppColors.primary500, width: 1),
          ),
        ),
      ),
    );
  }

  Widget _buildFilterBar() {
    return Container(
      color: AppColors.surface,
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
        child: Row(
          children: [
            if (_activeView != null) ...[
              _TaskFilterChip(
                label: _activeView == AppRoutes.viewOverdue
                    ? 'Overdue'
                    : 'Due today',
                isActive: true,
                icon: LucideIcons.alertTriangle,
                onTap: _clearAllFilters,
              ),
              const SizedBox(width: 6),
            ],
            _TaskFilterChip(
              label: _filters.assignedToLabel ?? 'Anyone',
              isActive: _filters.assignedToId != null,
              icon: LucideIcons.user,
              onTap: _showAssigneeFilter,
            ),
            const SizedBox(width: 6),
            _TaskFilterChip(
              // With more than one status selected there is no single label to
              // show, and printing one of them would name a filter narrower
              // than the list actually is.
              label: switch (_filters.statuses.length) {
                0 => 'Status',
                1 => _filters.statuses.first.label,
                final n => '$n statuses',
              },
              isActive: _filters.statuses.isNotEmpty,
              onTap: _showStatusFilter,
            ),
            const SizedBox(width: 6),
            _TaskFilterChip(
              label: _filters.priority?.label ?? 'Priority',
              isActive: _filters.priority != null,
              onTap: _showPriorityFilter,
            ),
            if (_filters.isActive) ...[
              const SizedBox(width: 6),
              GestureDetector(
                onTap: _clearAllFilters,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 8,
                    vertical: 5,
                  ),
                  decoration: BoxDecoration(
                    color: AppColors.danger100,
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(LucideIcons.x, size: 12, color: AppColors.danger600),
                      const SizedBox(width: 3),
                      Text(
                        'Clear',
                        style: AppTypography.caption.copyWith(
                          color: AppColors.danger600,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  /// Compact summary line so overdue/today counts are visible without scrolling
  /// past the Completed accordion.
  Widget _buildHeaderSummary(int overdue, int today, int upcoming, int total) {
    final parts = <String>[];
    if (overdue > 0) parts.add('$overdue overdue');
    if (today > 0) parts.add('$today today');
    if (upcoming > 0) parts.add('$upcoming upcoming');
    final summary = parts.isEmpty
        ? '$total task${total == 1 ? '' : 's'}'
        : parts.join(' · ');
    return Container(
      width: double.infinity,
      color: AppColors.surfaceDim,
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
      child: Text(
        summary,
        style: AppTypography.caption.copyWith(
          color: overdue > 0 ? AppColors.danger600 : AppColors.textSecondary,
          fontWeight: overdue > 0 ? FontWeight.w600 : null,
        ),
      ),
    );
  }

  Widget _buildCalendarView(List<Task> allTasks) {
    final taskDates = _getTaskDates(allTasks);
    final tasksForSelectedDate = _getTasksForSelectedDate(allTasks);

    return Column(
      children: [
        // Calendar
        Container(
          color: AppColors.surface,
          child: TableCalendar(
            firstDay: DateTime.utc(2020, 1, 1),
            lastDay: DateTime.utc(2030, 12, 31),
            focusedDay: _focusedDay,
            selectedDayPredicate: (day) => _isSameDay(_selectedDay, day),
            calendarFormat: _calendarFormat,
            startingDayOfWeek: StartingDayOfWeek.sunday,
            onDaySelected: (selectedDay, focusedDay) {
              setState(() {
                _selectedDay = selectedDay;
                _focusedDay = focusedDay;
              });
            },
            onFormatChanged: (format) {
              setState(() {
                _calendarFormat = format;
              });
            },
            onPageChanged: (focusedDay) {
              _focusedDay = focusedDay;
              // Refetch the new month so markers reflect what's actually due.
              // Without this, navigating past page 1 looks empty even when
              // tasks exist in that month.
              _fetchMonthForCalendar(focusedDay);
            },
            calendarStyle: CalendarStyle(
              todayDecoration: BoxDecoration(
                color: AppColors.primary100,
                shape: BoxShape.circle,
              ),
              todayTextStyle: AppTypography.body.copyWith(
                color: AppColors.primary700,
                fontWeight: FontWeight.w600,
              ),
              selectedDecoration: BoxDecoration(
                color: AppColors.primary600,
                shape: BoxShape.circle,
              ),
              selectedTextStyle: AppTypography.body.copyWith(
                color: Colors.white,
                fontWeight: FontWeight.w600,
              ),
              defaultTextStyle: AppTypography.body,
              weekendTextStyle: AppTypography.body,
              outsideTextStyle: AppTypography.body.copyWith(
                color: AppColors.gray300,
              ),
              markerDecoration: BoxDecoration(
                color: AppColors.primary500,
                shape: BoxShape.circle,
              ),
              markersMaxCount: 3,
              markerSize: 5,
              markerMargin: const EdgeInsets.symmetric(horizontal: 1),
            ),
            headerStyle: HeaderStyle(
              titleCentered: true,
              formatButtonVisible: false,
              titleTextStyle: AppTypography.h3,
              leftChevronIcon: Icon(
                LucideIcons.chevronLeft,
                size: 22,
                color: AppColors.textSecondary,
              ),
              rightChevronIcon: Icon(
                LucideIcons.chevronRight,
                size: 22,
                color: AppColors.textSecondary,
              ),
            ),
            daysOfWeekStyle: DaysOfWeekStyle(
              weekdayStyle: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
              weekendStyle: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            calendarBuilders: CalendarBuilders(
              markerBuilder: (context, date, events) {
                if (taskDates.contains(_formatDateKey(date))) {
                  final tasksOnDate = allTasks
                      .where(
                        (t) =>
                            t.dueDate != null && _isSameDay(t.dueDate!, date),
                      )
                      .toList();
                  final hasOverdue = tasksOnDate.any(
                    (t) =>
                        !t.completed &&
                        t.dueDate != null &&
                        t.dueDate!.isBefore(DateTime.now()),
                  );

                  return Positioned(
                    bottom: 4,
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Container(
                          width: 6,
                          height: 6,
                          decoration: BoxDecoration(
                            color: hasOverdue
                                ? AppColors.danger500
                                : AppColors.primary500,
                            shape: BoxShape.circle,
                          ),
                        ),
                        if (tasksOnDate.length > 1) ...[
                          const SizedBox(width: 2),
                          Container(
                            width: 4,
                            height: 4,
                            decoration: BoxDecoration(
                              color: AppColors.gray400,
                              shape: BoxShape.circle,
                            ),
                          ),
                        ],
                      ],
                    ),
                  );
                }
                return null;
              },
            ),
          ),
        ),

        // Section Header
        Container(
          width: double.infinity,
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
          color: AppColors.surfaceDim,
          child: Text(
            _getSelectedDateLabel(),
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
        ),

        // Tasks for selected date
        Expanded(
          child: tasksForSelectedDate.isEmpty
              ? _buildEmptyDateState()
              : ListView.builder(
                  padding: const EdgeInsets.only(bottom: 100),
                  itemCount: tasksForSelectedDate.length,
                  itemBuilder: (context, index) {
                    final task = tasksForSelectedDate[index];
                    return TaskRow(
                      task: task,
                      onToggle: () => _toggleTask(task),
                      onTap: () => _showTaskDetail(task),
                      onDelete: () => _deleteTask(task),
                      onComplete: task.completed
                          ? null
                          : () => _toggleTask(task),
                    );
                  },
                ),
        ),
      ],
    );
  }

  Widget _buildListView(List<Task> allTasks, bool isLoading) {
    final overdueTasks = ref.watch(overdueTasksProvider);
    final todayTasks = ref.watch(todayTasksProvider);
    final upcomingTasks = ref.watch(upcomingTasksProvider);
    final completedTasks = ref.watch(completedTasksProvider);
    final noDueDateTasks = ref.watch(noDueDateTasksProvider);

    // Every group that can render below has to count here, completed included.
    // Omitting it told any user whose visible tasks were all completed that
    // they had none, and made the Completed filter answer "no matching tasks"
    // for rows the server had just returned.
    final hasAnyTasks =
        overdueTasks.isNotEmpty ||
        todayTasks.isNotEmpty ||
        upcomingTasks.isNotEmpty ||
        noDueDateTasks.isNotEmpty ||
        completedTasks.isNotEmpty;

    if (!hasAnyTasks && !isLoading) {
      return _buildAllCaughtUpState();
    }

    final hasMore = ref.watch(tasksProvider).value?.hasMore ?? false;

    return RefreshIndicator(
      onRefresh: () async {
        await ref.read(tasksProvider.notifier).refresh();
      },
      child: SingleChildScrollView(
        controller: _scrollController,
        padding: const EdgeInsets.only(bottom: 100),
        child: Column(
          children: [
            _buildHeaderSummary(
              overdueTasks.length,
              todayTasks.length,
              upcomingTasks.length,
              allTasks.length,
            ),

            if (overdueTasks.isNotEmpty)
              TaskGroup(
                title: 'Overdue',
                variant: 'danger',
                tasks: overdueTasks,
                onToggle: _toggleTask,
                onTap: _showTaskDetail,
                onDelete: _deleteTask,
                onComplete: (t) => _toggleTask(t),
              ),

            if (todayTasks.isNotEmpty)
              TaskGroup(
                title: 'Today',
                variant: 'warning',
                tasks: todayTasks,
                onToggle: _toggleTask,
                onTap: _showTaskDetail,
                onDelete: _deleteTask,
                onComplete: (t) => _toggleTask(t),
              ),

            if (upcomingTasks.isNotEmpty)
              TaskGroup(
                title: 'Upcoming',
                variant: 'default',
                tasks: upcomingTasks,
                onToggle: _toggleTask,
                onTap: _showTaskDetail,
                onDelete: _deleteTask,
                onComplete: (t) => _toggleTask(t),
              ),

            if (noDueDateTasks.isNotEmpty)
              TaskGroup(
                title: 'No Due Date',
                variant: 'default',
                tasks: noDueDateTasks,
                onToggle: _toggleTask,
                onTap: _showTaskDetail,
                onDelete: _deleteTask,
                onComplete: (t) => _toggleTask(t),
              ),

            if (completedTasks.isNotEmpty)
              TaskGroup(
                title: 'Completed',
                variant: 'default',
                tasks: completedTasks,
                // Collapsed by default so it does not bury the live work, but
                // expanded when it is the only group, since a lone collapsed
                // header reads as an empty screen.
                initiallyExpanded:
                    overdueTasks.isEmpty &&
                    todayTasks.isEmpty &&
                    upcomingTasks.isEmpty &&
                    noDueDateTasks.isEmpty,
                onToggle: _toggleTask,
                onTap: _showTaskDetail,
                onDelete: _deleteTask,
                onComplete: (t) => _toggleTask(t),
              ),

            if (hasMore && isLoading)
              const Padding(
                padding: EdgeInsets.all(16),
                child: Center(child: CircularProgressIndicator()),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildEmptyDateState() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 56,
              height: 56,
              decoration: BoxDecoration(
                color: AppColors.gray100,
                shape: BoxShape.circle,
              ),
              child: Icon(
                LucideIcons.calendarCheck,
                size: 28,
                color: AppColors.gray400,
              ),
            ),
            const SizedBox(height: 16),
            Text(
              'No tasks for this date',
              style: AppTypography.label.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 8),
            GestureDetector(
              onTap: () => _navigateToCreateTask(forDay: _selectedDay),
              child: Text(
                'Add a task',
                style: AppTypography.label.copyWith(
                  color: AppColors.primary600,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildAllCaughtUpState() {
    final isFiltered = _filters.isActive;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 80,
              height: 80,
              decoration: BoxDecoration(
                color: AppColors.success100,
                shape: BoxShape.circle,
              ),
              child: Icon(
                isFiltered ? LucideIcons.search : LucideIcons.checkCircle2,
                size: 40,
                color: AppColors.success600,
              ),
            ),
            const SizedBox(height: 20),
            Text(
              isFiltered ? 'No matching tasks' : 'All caught up!',
              style: AppTypography.h2.copyWith(color: AppColors.textPrimary),
            ),
            const SizedBox(height: 8),
            Text(
              isFiltered
                  ? 'Try adjusting your search or filters.'
                  : 'No pending tasks. Create a new task to get started.',
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 24),
            PrimaryButton(
              label: isFiltered ? 'Clear filters' : 'Add Task',
              icon: isFiltered ? LucideIcons.x : LucideIcons.plus,
              onPressed: isFiltered ? _clearAllFilters : _navigateToCreateTask,
            ),
          ],
        ),
      ),
    );
  }

  String _getSelectedDateLabel() {
    final now = DateTime.now();

    if (_isSameDay(_selectedDay, now)) {
      return 'Today';
    } else if (_isSameDay(_selectedDay, now.add(const Duration(days: 1)))) {
      return 'Tomorrow';
    } else if (_isSameDay(
      _selectedDay,
      now.subtract(const Duration(days: 1)),
    )) {
      return 'Yesterday';
    } else {
      final months = [
        'January',
        'February',
        'March',
        'April',
        'May',
        'June',
        'July',
        'August',
        'September',
        'October',
        'November',
        'December',
      ];
      return '${months[_selectedDay.month - 1]} ${_selectedDay.day}';
    }
  }

  /// [forDay] carries the calendar cell the user tapped, so the form opens on
  /// that date instead of making them pick it again.
  Future<void> _navigateToCreateTask({DateTime? forDay}) async {
    final path = forDay == null
        ? '/tasks/create'
        : '/tasks/create?due=${_formatDateKey(forDay)}';
    final result = await context.push(path);
    if (result == true && mounted) {
      ref.read(tasksProvider.notifier).refresh();
    }
  }

  Future<void> _toggleTask(Task task) async {
    final response = await ref
        .read(tasksProvider.notifier)
        .toggleTaskStatus(task);

    if (mounted) {
      if (response.success) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              task.completed ? 'Task marked as incomplete' : 'Task completed',
            ),
            behavior: SnackBarBehavior.floating,
          ),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(response.message ?? 'Failed to update task'),
            behavior: SnackBarBehavior.floating,
            backgroundColor: AppColors.danger600,
          ),
        );
      }
    }
  }

  void _showTaskDetail(Task task) {
    context.push('/tasks/${task.id}');
  }

  Future<void> _deleteTask(Task task) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete Task?'),
        content: Text('Are you sure you want to delete "${task.title}"?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text('Delete', style: TextStyle(color: AppColors.danger600)),
          ),
        ],
      ),
    );

    if (confirmed == true && mounted) {
      final response = await ref
          .read(tasksProvider.notifier)
          .deleteTask(task.id);

      if (mounted) {
        if (response.success) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('Task deleted'),
              behavior: SnackBarBehavior.floating,
            ),
          );
        } else {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text(response.message ?? 'Failed to delete task'),
              behavior: SnackBarBehavior.floating,
              backgroundColor: AppColors.danger600,
            ),
          );
        }
      }
    }
  }

  bool _isSameDay(DateTime a, DateTime b) {
    return a.year == b.year && a.month == b.month && a.day == b.day;
  }

  String _formatDateKey(DateTime date) {
    return '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}';
  }

  // ─── Filter sheets ────────────────────────────────────────────────────────

  void _showAssigneeFilter() {
    final profileId = ref.read(profileProvider).value?.id;
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _TaskFilterSheet(
        title: 'Filter by Owner',
        options: [
          _TaskFilterOption(
            label: 'Anyone',
            isSelected: _filters.assignedToId == null,
            onTap: () {
              _applyFilters(_filters.cleared(assignedTo: true));
              Navigator.pop(context);
            },
          ),
          if (profileId != null)
            _TaskFilterOption(
              label: 'My tasks',
              isSelected: _filters.assignedToId == profileId,
              onTap: () {
                _applyFilters(
                  _filters.copyWith(
                    assignedToId: profileId,
                    assignedToLabel: 'Me',
                  ),
                );
                Navigator.pop(context);
              },
            ),
        ],
      ),
    );
  }

  void _showStatusFilter() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _TaskFilterSheet(
        title: 'Filter by Status',
        options: [
          _TaskFilterOption(
            label: 'All statuses',
            isSelected: _filters.status == null,
            onTap: () {
              _applyFilters(_filters.cleared(status: true));
              Navigator.pop(context);
            },
          ),
          ...TaskStatus.values.map(
            (s) => _TaskFilterOption(
              label: s.label,
              isSelected: _filters.status == s,
              onTap: () {
                _applyFilters(_filters.copyWith(status: s));
                Navigator.pop(context);
              },
            ),
          ),
        ],
      ),
    );
  }

  void _showPriorityFilter() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _TaskFilterSheet(
        title: 'Filter by Priority',
        options: [
          _TaskFilterOption(
            label: 'All priorities',
            isSelected: _filters.priority == null,
            onTap: () {
              _applyFilters(_filters.cleared(priority: true));
              Navigator.pop(context);
            },
          ),
          // Task model only supports Low/Medium/High at the backend level.
          ...Priority.values
              .where((p) => p != Priority.urgent)
              .map(
                (p) => _TaskFilterOption(
                  label: p.label,
                  isSelected: _filters.priority == p,
                  onTap: () {
                    _applyFilters(_filters.copyWith(priority: p));
                    Navigator.pop(context);
                  },
                ),
              ),
        ],
      ),
    );
  }
}

class _TaskFilterChip extends StatelessWidget {
  final String label;
  final bool isActive;
  final IconData? icon;
  final VoidCallback onTap;

  const _TaskFilterChip({
    required this.label,
    required this.isActive,
    required this.onTap,
    this.icon,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: isActive ? AppColors.primary100 : AppColors.gray100,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: isActive ? AppColors.primary400 : Colors.transparent,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (icon != null) ...[
              Icon(
                icon,
                size: 13,
                color: isActive
                    ? AppColors.primary700
                    : AppColors.textSecondary,
              ),
              const SizedBox(width: 5),
            ],
            Text(
              label,
              style: AppTypography.caption.copyWith(
                color: isActive
                    ? AppColors.primary700
                    : AppColors.textSecondary,
                fontWeight: isActive ? FontWeight.w600 : null,
              ),
            ),
            const SizedBox(width: 3),
            Icon(
              LucideIcons.chevronDown,
              size: 13,
              color: isActive ? AppColors.primary700 : AppColors.textTertiary,
            ),
          ],
        ),
      ),
    );
  }
}

class _TaskFilterOption {
  final String label;
  final bool isSelected;
  final VoidCallback onTap;

  const _TaskFilterOption({
    required this.label,
    required this.isSelected,
    required this.onTap,
  });
}

class _TaskFilterSheet extends StatelessWidget {
  final String title;
  final List<_TaskFilterOption> options;

  const _TaskFilterSheet({required this.title, required this.options});

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 12, 20, 8),
              child: Row(
                children: [
                  Text(title, style: AppTypography.h3),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(LucideIcons.x, size: 20),
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
            ),
            const Divider(height: 1),
            ...options.map(
              (opt) => ListTile(
                title: Text(opt.label),
                trailing: opt.isSelected
                    ? Icon(LucideIcons.check, color: AppColors.primary600)
                    : null,
                onTap: opt.onTap,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
